# Quick Start: HERE API Setup

## 3-Minute Setup

### 1. Get API Key (2 minutes)
1. Go to https://platform.here.com/ → Sign up
2. Create project: `brew-crawl-planner`
3. Generate **REST API Key**
4. Copy the key

### 2. Add to .env (30 seconds)
Edit `backend/.env`:
```bash
HERE_API_KEY=paste-your-key-here
```

### 3. Test (30 seconds)
```bash
cd /Users/jmarino/CW/brew-crawl-planner
./start.sh
```

Open browser: http://localhost:8000
- Enter: **Oakland**
- Stops: **3**
- Click **Generate Crawl**

**Expected:** Real addresses like "2325 Broadway, Oakland, CA, 94612"

---

## What You Get

✅ **250,000 free requests/month** (enough for 40,000+ searches)  
✅ **Verified addresses** for all breweries  
✅ **Phone numbers & websites**  
✅ **Opening hours** (when available)  
✅ **11x cheaper** than Google Places ($1.50 vs $17 per 1,000 after free tier)

---

## How to Verify It's Working

### Check 1: Look for Address Format
**WITH HERE API:**
```
Drake's Dealership
Address: 2325 Broadway, Oakland, CA, 94612
Phone: +1-510-568-2739
```

**WITHOUT HERE API (Tavily fallback):**
```
Drake's Dealership is a popular spot...
[May not have full address]
```

### Check 2: Response Prefix
Look in the results for:
- `"Oakland breweries (verified addresses)"` ← HERE API working ✅
- `"Oakland open breweries"` ← Tavily fallback ⚠️

---

## Troubleshooting

**No results?**
1. Check API key in `.env` is correct
2. Restart server: `./start.sh`
3. Check https://platform.here.com/ → Usage dashboard

**Still using Tavily?**
- API key might be invalid or expired
- Check for typos in `.env`
- Verify key hasn't been revoked

---

## Next Steps

See **HERE_API_SETUP.md** for:
- Detailed API documentation
- Cost analysis
- Advanced configuration
- Troubleshooting guide
- Migration notes

---

## Support

- Platform: https://platform.here.com/
- Docs: https://developer.here.com/documentation
- Forum: https://developer.here.com/forum
