"""
AI Assistant for Discord - Context-aware chat assistant similar to Groq.
Tracks chat context and answers questions about what's happening in the conversation.
"""

import logging
import os
from typing import List, Dict, Optional
import aiohttp
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ChatContext:
    """Manages chat context for a channel."""
    
    def __init__(self, max_messages: int = 50, max_age_hours: int = 24):
        self.max_messages = max_messages
        self.max_age_hours = max_age_hours
        self.messages: List[Dict] = []
    
    def add_message(self, author: str, content: str, timestamp: datetime, attachments: List[str] = None):
        """Add a message to context."""
        # Remove old messages
        cutoff_time = datetime.utcnow() - timedelta(hours=self.max_age_hours)
        self.messages = [
            msg for msg in self.messages 
            if msg['timestamp'] > cutoff_time
        ]
        
        # Add new message
        self.messages.append({
            'author': author,
            'content': content,
            'timestamp': timestamp,
            'attachments': attachments or []
        })
        
        # Keep only recent messages
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def get_recent_context(self, limit: int = 20) -> List[Dict]:
        """Get recent messages for context."""
        return self.messages[-limit:]
    
    def format_context(self, limit: int = 20) -> str:
        """Format recent messages as a readable context string."""
        recent = self.get_recent_context(limit)
        if not recent:
            return "No recent messages in this channel."
        
        lines = []
        for msg in recent:
            timestamp = msg['timestamp'].strftime("%H:%M")
            author = msg['author']
            content = msg['content'][:200]  # Truncate long messages
            lines.append(f"[{timestamp}] {author}: {content}")
        
        return "\n".join(lines)


class AIAssistant:
    """AI Assistant that answers questions about chat context."""
    
    def __init__(self, api_key: Optional[str] = None, api_provider: str = "openai"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")
        self.api_provider = api_provider.lower()
        self.base_url = self._get_base_url()
        self.model = self._get_model()
        
    def _get_base_url(self) -> str:
        """Get API base URL based on provider."""
        if self.api_provider == "groq":
            return "https://api.groq.com/openai/v1"
        elif self.api_provider == "openai":
            return "https://api.openai.com/v1"
        elif self.api_provider == "anthropic":
            return "https://api.anthropic.com/v1"
        else:
            return "https://api.openai.com/v1"  # Default to OpenAI
    
    def _get_model(self) -> str:
        """Get model name based on provider."""
        if self.api_provider == "groq":
            return os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
        elif self.api_provider == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        elif self.api_provider == "anthropic":
            return os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        else:
            return "gpt-4o-mini"
    
    async def ask_question(
        self, 
        question: str, 
        context: str,
        channel_name: str = "general",
        server_name: str = "Discord Server"
    ) -> Optional[str]:
        """Ask the AI a question with chat context."""
        if not self.api_key:
            logger.warning("No API key configured for AI assistant")
            return None
        
        try:
            # Build system prompt
            system_prompt = f"""You are a helpful AI assistant in a Discord server called "{server_name}". 
You can see recent messages from the #{channel_name} channel and answer questions about what's happening in the conversation.

Your role:
- Answer questions about recent chat activity
- Summarize what people are discussing
- Help users understand context they might have missed
- Be concise and friendly
- If you don't have enough context, say so

Recent chat context:
{context}

Answer the user's question based on the context above. Be helpful and concise."""

            # Prepare API request
            if self.api_provider == "anthropic":
                return await self._call_anthropic(system_prompt, question)
            else:
                return await self._call_openai_compatible(system_prompt, question)
                
        except Exception as e:
            logger.error(f"Error calling AI API: {e}", exc_info=True)
            return None
    
    async def _call_openai_compatible(self, system_prompt: str, question: str) -> Optional[str]:
        """Call OpenAI-compatible API (OpenAI, Groq, etc.)."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                else:
                    error_text = await response.text()
                    logger.error(f"API error {response.status}: {error_text}")
                    return None
    
    async def _call_anthropic(self, system_prompt: str, question: str) -> Optional[str]:
        """Call Anthropic Claude API."""
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 500,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": question}
            ]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("content", [{}])[0].get("text", "").strip()
                else:
                    error_text = await response.text()
                    logger.error(f"Anthropic API error {response.status}: {error_text}")
                    return None


# Global chat context storage
chat_contexts: Dict[int, ChatContext] = {}  # channel_id -> ChatContext

def get_chat_context(channel_id: int) -> ChatContext:
    """Get or create chat context for a channel."""
    if channel_id not in chat_contexts:
        chat_contexts[channel_id] = ChatContext()
    return chat_contexts[channel_id]
