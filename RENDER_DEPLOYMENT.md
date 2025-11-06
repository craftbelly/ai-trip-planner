# Render Deployment Guide - HERE API Integration

## Quick Deployment Steps

Your code is now on GitHub and ready to deploy! Render will automatically detect the push and rebuild.

---

## Environment Variables to Set in Render

Go to your Render dashboard → Your service → Environment tab

### Required Variables

```bash
# LLM Provider (REQUIRED - choose one)
OPENROUTER_API_KEY=your-actual-openrouter-key
OPENROUTER_MODEL=openai/gpt-4o-mini

# Phoenix Tracing (REQUIRED for observability)
PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com
PHOENIX_API_KEY=your-actual-phoenix-key
```

### Optional but Recommended (HERE API)

```bash
# HERE API (for verified brewery addresses)
HERE_API_KEY=your-actual-here-api-key

# Tavily (fallback for web search)
TAVILY_API_KEY=your-actual-tavily-key
```

### Optional - RAG Configuration

```bash
# Enable RAG with local guides
ENABLE_RAG=1

# Use free HuggingFace embeddings (recommended)
USE_HUGGINGFACE_EMBEDDINGS=1
HUGGINGFACE_EMBED_MODEL=all-MiniLM-L6-v2
```

---

## Step-by-Step: Add HERE API Key to Render

### 1. Go to Render Dashboard
- Navigate to: https://dashboard.render.com/
- Select your `brew-crawl-planner` service

### 2. Click "Environment" Tab
- On the left sidebar, click **Environment**

### 3. Add New Environment Variable
Click **"Add Environment Variable"** and add:

**Key:** `HERE_API_KEY`  
**Value:** `EbfFzRvR7OW2zqPRyqS2gCOI2RTHB6wY1qa4Pjr62Qc`

(This is your actual HERE API key from the local .env file)

### 4. Save Changes
- Click **"Save Changes"**
- Render will automatically redeploy with the new variable

### 5. Wait for Deployment
- Watch the **Logs** tab for deployment progress
- Look for: `"✅ Phoenix tracing initialized successfully"`
- Deployment typically takes 2-3 minutes

---

## Verify HERE API is Working on Render

### Check 1: View Logs
In Render dashboard → Logs tab, look for:
```
🤗 Loading HuggingFace embeddings: all-MiniLM-L6-v2
✅ HuggingFace embeddings loaded successfully
📚 Indexing 157 documents...
✅ Vector store ready with 157 documents
```

### Check 2: Test API
Once deployed, visit your Render URL:
```
https://your-app-name.onrender.com
```

### Check 3: Test Brewery Search
Use the UI or curl:
```bash
curl -X POST https://your-app-name.onrender.com/plan-crawl \
  -H "Content-Type: application/json" \
  -d '{"destination":"Oakland","duration":"3","interests":"IPA"}'
```

**Look for:**
- ✅ Real brewery names (Drake's, Original Pattern, etc.)
- ✅ Structured addresses (street, city, state, zip)
- ✅ "breweries (verified addresses)" in response

---

## Full Environment Variable List for Render

Copy these into Render Environment tab (replace placeholders with your actual keys):

```bash
# LLM Provider
OPENROUTER_API_KEY=sk-or-v1-your-actual-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini

# Phoenix Tracing
PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com
PHOENIX_API_KEY=your-actual-phoenix-api-key

# HERE API (NEW!)
HERE_API_KEY=EbfFzRvR7OW2zqPRyqS2gCOI2RTHB6wY1qa4Pjr62Qc

# Tavily API
TAVILY_API_KEY=tvly-dev-your-actual-key

# RAG Configuration
ENABLE_RAG=1
USE_HUGGINGFACE_EMBEDDINGS=1
HUGGINGFACE_EMBED_MODEL=all-MiniLM-L6-v2
```

**Note:** You should already have OPENROUTER_API_KEY, PHOENIX_API_KEY, and TAVILY_API_KEY set from previous deployments. Just add the HERE_API_KEY.

---

## Expected Deployment Behavior

### With HERE API Key Set
1. Server starts
2. HuggingFace embeddings load (free, local)
3. 157 Bay Area breweries indexed
4. HERE API available for brewery searches
5. Returns verified addresses

**Example Output:**
```
Stop 1: Original Pattern Brewing Co.
Address: 4th St, Oakland, CA 94607-4332
```

### Without HERE API Key
1. Server starts normally
2. Falls back to Tavily web search
3. Then falls back to LLM generation
4. May have incomplete addresses

---

## Troubleshooting Render Deployment

### Issue 1: Deployment Failed
**Check:**
- Render Logs tab for error messages
- Ensure all REQUIRED variables are set
- Verify Python version compatibility (3.9+)

**Common fixes:**
- Check `requirements.txt` has all dependencies
- Ensure `render.yaml` is properly configured

### Issue 2: "Module not found" errors
**Cause:** Missing dependency in requirements.txt

**Fix:** Already included in latest commit:
```
sentence-transformers>=2.2.0
langchain-huggingface>=0.0.1
```

### Issue 3: HERE API not working on Render
**Check:**
1. Environment variable `HERE_API_KEY` is set in Render dashboard
2. No typos in the key value
3. Check logs for: "HERE API" or "discover.search.hereapi.com"

**Test directly:**
```bash
# From Render shell (if available)
python3 -c "import os; print(f'HERE_API_KEY present: {bool(os.getenv(\"HERE_API_KEY\"))}')"
```

### Issue 4: Slow first request
**Expected behavior:** First request after deployment takes 10-20 seconds
- Loading embeddings model (~90MB)
- Indexing 157 breweries
- Initializing LLM connection

**Solution:** This is normal. Subsequent requests are much faster.

### Issue 5: Memory errors on Render
**Cause:** Free tier Render has 512MB RAM limit

**Solutions:**
- Use HuggingFace embeddings (already configured) instead of OpenAI
- Reduce `HUGGINGFACE_EMBED_MODEL` to smaller model if needed
- Consider upgrading Render plan

---

## Render Pricing Considerations

### Current Setup (Optimized for Free Tier)
- ✅ HuggingFace embeddings: Run locally, no API cost
- ✅ HERE API: 250,000 free requests/month
- ✅ Render Free tier: 750 hours/month
- 💰 OpenRouter: Pay as you go (~$5-10/month)
- 💰 Phoenix: Free tier available

### If You Exceed Free Tiers
- Render Starter: $7/month (more RAM, faster)
- HERE API: $1.50 per 1,000 requests (after 250K)
- OpenRouter: ~$0.60 per 1M tokens

**Total estimated cost:** $10-20/month for moderate usage

---

## Post-Deployment Testing Checklist

### ✅ Basic Health Check
```bash
curl https://your-app.onrender.com/health
# Expected: {"status":"healthy","service":"brew-crawl-planner"}
```

### ✅ Oakland Brewery Test
```bash
curl -X POST https://your-app.onrender.com/plan-crawl \
  -H "Content-Type: application/json" \
  -d '{"destination":"Oakland","duration":"3","interests":"IPA"}'
```

**Expected in response:**
- Original Pattern Brewing Co.
- Address: 4th St, Oakland, CA 94607-4332
- Cellarmaker - Oakland
- Drake's Dealership

### ✅ Berkeley Brewery Test
```bash
curl -X POST https://your-app.onrender.com/plan-crawl \
  -H "Content-Type: application/json" \
  -d '{"destination":"Berkeley","duration":"3","interests":"craft beer"}'
```

**Expected:**
- Fieldwork Brewing Company
- Triple Rock Brewery
- Jupiter

### ✅ UI Test
Visit: `https://your-app.onrender.com/`
- Enter "Oakland" as destination
- Set 3 stops
- Click "Generate Crawl"
- Should see breweries with addresses

---

## Monitoring Production

### Render Dashboard
- **Logs:** Real-time server logs
- **Metrics:** CPU, memory, request rate
- **Events:** Deployments, restarts

### Phoenix Dashboard
- **Traces:** All LLM calls and tool usage
- **Analytics:** Response times, costs
- **Debugging:** Tool call arguments and results

Visit: https://app.phoenix.arize.com/

### HERE API Dashboard
- **Usage:** Track API calls
- **Quota:** Monitor free tier remaining
- **Billing:** View costs if over free tier

Visit: https://platform.here.com/

---

## Rollback Plan

If deployment has issues, you can roll back:

### Option 1: Render Dashboard Rollback
1. Go to Render dashboard → Your service
2. Click **"Manual Deploy"** tab
3. Select previous commit: `42647e2`
4. Click **"Deploy"**

### Option 2: Git Revert
```bash
cd /Users/jmarino/CW/brew-crawl-planner
git revert HEAD
git push origin main
```

Render will auto-deploy the reverted version.

---

## Next Steps After Deployment

### Immediate (Today)
1. ✅ Add HERE_API_KEY to Render environment variables
2. ✅ Wait for Render to redeploy (2-3 minutes)
3. ✅ Test Oakland brewery search on live site
4. ✅ Verify addresses appear in results

### Short Term (This Week)
1. Monitor HERE API usage in dashboard
2. Test with different cities (SF, Berkeley, San Jose)
3. Check Phoenix traces for tool calls
4. Share live URL with users for feedback

### Medium Term
1. Add caching to reduce API calls
2. Implement rate limiting
3. Add error tracking (e.g., Sentry)
4. Optimize response times

---

## Support Resources

### Render
- Dashboard: https://dashboard.render.com/
- Docs: https://render.com/docs
- Status: https://status.render.com/

### HERE API
- Dashboard: https://platform.here.com/
- Docs: https://developer.here.com/documentation

### Phoenix
- Dashboard: https://app.phoenix.arize.com/
- Docs: https://docs.arize.com/phoenix

### Project Documentation
- HERE API Setup: `HERE_API_SETUP.md`
- Quick Start: `QUICK_START_HERE_API.md`
- Changes: `CHANGES_HERE_API.md`
- Success Tests: `HERE_API_SUCCESS.md`

---

## Quick Reference

### Your Render Service
- GitHub Repo: https://github.com/craftbelly/ai-trip-planner
- Latest Commit: `1295f91` - Add HERE API integration
- Branch: `main`

### Environment Variables to Add
Just add this one (if others are already set):
```
HERE_API_KEY=EbfFzRvR7OW2zqPRyqS2gCOI2RTHB6wY1qa4Pjr62Qc
```

### After Deployment URL
Your live site will be at:
```
https://[your-service-name].onrender.com
```

Test with:
```
https://[your-service-name].onrender.com/health
```

---

## Summary

✅ Code pushed to GitHub  
🔄 Render will auto-deploy  
🔑 Add HERE_API_KEY environment variable in Render dashboard  
⏱️ Wait 2-3 minutes for deployment  
🧪 Test Oakland brewery search on live site  
📍 Verify addresses appear in results  

**Your HERE API integration is production-ready!** 🍺
