# 🎮 Backrooms Pirate Ship - Complete Feature List

## 📊 **Batch 1: Reviews, Tags, Search & Health Monitoring**

### User Reviews & Ratings System
- `/review <game> <rating> [review_text]` - Write reviews with 1-5 star ratings
- `/reviews <game>` - View all reviews for a game with average ratings
- Community-driven quality feedback for all games

### Game Tags & Categories
- `/addtag <game> <tags>` - Add tags to games (Admin only)
- `/tags <tag>` - Search games by tags (Horror, RPG, Multiplayer, etc.)
- Organize games by genres and categories

### Trending System
- `/trending` - View trending games based on:
  - Downloads (3x weight)
  - Library additions (2x weight)
  - Reviews (1x weight)
- Real-time popularity metrics

### Advanced Search
- `/advancedsearch <query> [min_rating] [tag] [sort]` - Multi-filter search
- Sort options: Relevance, Rating, Downloads, Newest
- Filter by minimum rating and tags
- Combines all game metadata for best results

### Link Health Monitor
- Automatic daily health checks for all download links
- `/linkhealth` - View games with broken links
- `/checkhealth` - Manually trigger health check (Admin only)
- Tracks broken links per game with timestamps

---

## 🎬 **Batch 2: Media Integration, Backups & Webhooks**

### YouTube Trailers & Game Info
- `/trailer <game>` - Watch YouTube trailers directly
- `/gameinfo <game>` - Comprehensive game information:
  - Cover art thumbnail
  - Genres and platforms
  - Critics scores (IGDB)
  - User scores (IGDB)
  - Community ratings
  - Embedded trailer links

### Ratings & Scores
- IGDB integration for professional critic scores
- Aggregated user ratings from multiple sources
- Community ratings from our review system
- Side-by-side comparison display

### Auto-Backup System
- Automatic daily backups of all bot data
- `/backup` - Create manual backup (Admin only)
- Keeps last 7 backups automatically
- Backs up: RSS data, bot state, user data, reviews, tags, health data

### Webhook Notifications
- `/setwebhook <url>` - Set Discord webhook for notifications
- `/removewebhook` - Remove webhook subscription
- Automatic notifications for new game releases
- Personal webhook delivery system

### Dead Link Cleanup
- `/cleanup` - Generate report of games with broken links (Admin only)
- Shows up to 20 games needing attention
- Includes broken link counts per game
- Helps prioritize maintenance tasks

---

## 📚 **Batch 3: Collections, Bookmarks & Compatibility**

### Game Collections
- `/createcollection <name>` - Create custom game collections
- `/addtocollection <collection> <game>` - Add games to collections
- `/mycollections` - View all your collections
- Organize games by themes: "Horror Games", "Co-op", etc.
- Multiple collections per user

### Bookmarks System
- `/bookmark <game>` - Quick bookmark for later
- `/bookmarks` - View all bookmarked games with ratings
- Simple one-click save system
- Shows up to 25 bookmarks with ratings

### Compatibility Reports
- `/reportcompat <game> <status> [specs] [notes]` - Report compatibility
- Status options: ✅ Working, ⚠️ Issues, ❌ Broken
- Optional PC specs and detailed notes
- `/compatibility <game>` - View all compatibility reports
- Community-driven compatibility database

### Personalized Recommendations
- `/recommend` - Get AI-powered game suggestions
- Based on your library genres and tags
- Analyzes your collection patterns
- Shows top 10 recommendations with ratings
- Excludes games already in your library

---

## 📈 **Existing Features (Enhanced)**

### Library Management
- 📚 React to add games to your library
- `/mylibrary` - View your saved games
- Automatic update notifications for library games
- Library size tracking in stats

### Statistics & Analytics
- `/stats` - Comprehensive library statistics:
  - Total games count
  - Active vs archived games
  - Total downloads across all games
  - Users with libraries
  - Active game notifications
  - Top contributors
- `/downloadstats` - Top 10 most downloaded games
- Real-time tracking and updates

### Notifications & Voting
- `/notify <game>` - Subscribe to game notifications
- 👍 React on requests to vote
- `/toprequests` - View most voted requests
- Automatic notifications on game updates
- Up to 20 user mentions per update

### Similar Games
- `/similar <game>` - Find similar games via IGDB
- Shows up to 10 similar titles
- Includes ratings and genres
- Powered by IGDB recommendation engine

### RSS Auto-Posting
- Checks FitGirl RSS every 30 minutes
- Automatic game posting with full metadata
- Comprehensive duplicate detection
- IGDB and RAWG integration
- Automatic torrent file handling

### Dashboard & Status
- Real-time bot status messages
- Auto-updating dashboard every 15 minutes
- Shows recent games, top contributors, stats
- Status tracking: Starting, Online, Restarting

---

## 🛠️ **Admin Features**

### Moderation Tools
- `/addtag` - Add tags to games
- `/checkhealth` - Trigger health checks
- `/cleanup` - Dead link reports
- `/backup` - Manual backups
- Bulk operations support

### Monitoring & Reports
- Link health monitoring (daily)
- Auto-backup system (daily)
- Download statistics tracking
- User activity analytics
- Comprehensive error logging

---

## 🎯 **Commands Quick Reference**

### User Commands
```
/ping - Check bot status
/help - Show all commands
/search <query> - Basic search
/advancedsearch - Advanced multi-filter search
/stats - Library statistics
/latest - Recently added games
/random - Random game suggestion
/browse <genre> - Browse by genre
/trending - Trending games

/review - Write game review
/reviews - View game reviews
/tags <tag> - Search by tag
/trailer - Watch trailer
/gameinfo - Detailed game info
/similar - Find similar games

/mylibrary - Your game library
/bookmark - Bookmark a game
/bookmarks - View bookmarks
/notify - Subscribe to notifications
/recommend - Get recommendations

/createcollection - New collection
/addtocollection - Add to collection
/mycollections - View collections

/reportcompat - Report compatibility
/compatibility - View reports

/setwebhook - Setup webhooks
/removewebhook - Remove webhook

/requestgame - Request a game
/toprequests - Most voted requests
/downloadstats - Download statistics
```

### Admin Commands
```
/addgame - Add new game manually
/addtag - Tag games
/checkhealth - Health check
/cleanup - Broken links report
/backup - Create backup
/checkrss - Manual RSS check
/finddupes - Find duplicates
/refreshdashboard - Update dashboard
```

---

## 📊 **Data Persistence**

All data is automatically saved and persists across restarts:
- RSS seen posts (`fitgirl_seen_posts.json`)
- Bot state & contributors (`bot_state.json`)
- User libraries, votes, preferences (`user_data.json`)
- Game reviews (`reviews_data.json`)
- Game tags (`tags_data.json`)
- Link health data (`link_health_data.json`)
- Webhooks (`webhooks_data.json`)
- Collections (`collections_data.json`)
- Compatibility reports (`compatibility_data.json`)

---

## 🚀 **Performance Features**

- **Async/await** throughout for high performance
- **Queue system** for Playwright downloads
- **Cached data** for frequently accessed info
- **Batch operations** for efficiency
- **Rate limiting** to prevent API abuse
- **Error handling** with graceful degradation

---

## 🎨 **User Experience**

- Rich embeds with game artwork
- Interactive buttons and views
- Ephemeral messages for privacy
- Real-time updates and notifications
- Comprehensive help system
- Intuitive command structure

---

## 📱 **Integration Points**

- **IGDB API** - Game metadata, ratings, trailers
- **RAWG API** - Fallback game database
- **FitGirl RSS** - Auto-posting system
- **Discord Webhooks** - Personal notifications
- **BeautifulSoup4** - Web scraping
- **Playwright** - Browser automation

---

## ⚡ **Background Tasks**

- **RSS Monitor** - Every 30 minutes
- **Dashboard Update** - Every 15 minutes
- **Link Health Check** - Daily
- **Auto-Backup** - Daily
- **Playwright Queue** - Every 2 seconds

---

## 🔐 **Security & Permissions**

- Admin-only commands protected
- Ephemeral responses for sensitive data
- Webhook validation before saving
- Rate limiting on expensive operations
- Error messages sanitized
- User data privacy respected

---

## 📈 **Statistics Tracked**

- Game downloads per thread
- User library sizes
- Review counts and ratings
- Tag usage frequency
- Compatibility report counts
- Trending scores (downloads + libraries + reviews)
- Contributor activity
- Link health status
- Bookmark and collection usage

---

**Total Features Implemented: 50+**
**Commands Available: 30+**
**Background Tasks: 5**
**Data Files: 9**

All features are production-ready and deployed! 🎉
