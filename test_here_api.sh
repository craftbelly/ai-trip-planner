#!/bin/bash

# Test script for HERE API integration
# Tests Oakland brewery search with the new HERE API

echo "🍺 Testing Brew Crawl Planner with HERE API Integration"
echo "========================================================"
echo ""

# Check if server is running
echo "1️⃣  Checking server health..."
HEALTH=$(curl -s http://localhost:8000/health)
if [[ $HEALTH == *"healthy"* ]]; then
    echo "✅ Server is healthy"
else
    echo "❌ Server is not responding"
    exit 1
fi
echo ""

# Test Oakland brewery search
echo "2️⃣  Testing Oakland brewery search..."
echo "   (This will use HERE API if key is set, otherwise falls back to Tavily)"
echo ""

RESPONSE=$(curl -s -X POST http://localhost:8000/plan-crawl \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "Oakland",
    "duration": "3",
    "interests": "IPA"
  }')

echo "📊 Results:"
echo "----------"

# Check if response contains verified addresses (HERE API indicator)
if echo "$RESPONSE" | grep -q "verified addresses"; then
    echo "✅ HERE API is working! (Found 'verified addresses' in response)"
elif echo "$RESPONSE" | grep -q "open breweries"; then
    echo "⚠️  Using Tavily fallback (HERE API key not set or failed)"
else
    echo "ℹ️  Response received (check manually for details)"
fi
echo ""

# Check for specific Oakland breweries
echo "3️⃣  Checking for known Oakland breweries..."
if echo "$RESPONSE" | grep -qi "drake"; then
    echo "✅ Found Drake's (popular Oakland brewery)"
fi
if echo "$RESPONSE" | grep -qi "fieldwork\|original pattern\|cellarmaker"; then
    echo "✅ Found other Oakland breweries"
fi
echo ""

# Check for address format (street addresses indicate HERE API)
if echo "$RESPONSE" | grep -qE "[0-9]+ [A-Za-z]+ (St|Ave|Blvd|Way|Rd)"; then
    echo "✅ Full street addresses present (likely from HERE API)"
else
    echo "⚠️  No full addresses detected (may be using fallback)"
fi
echo ""

# Check for phone numbers (another HERE API indicator)
if echo "$RESPONSE" | grep -qE "\+?1?[-.]?\(?[0-9]{3}\)?[-.]?[0-9]{3}[-.]?[0-9]{4}"; then
    echo "✅ Phone numbers present (HERE API feature)"
fi
echo ""

echo "========================================================"
echo "📝 To view full response, run:"
echo "   curl -X POST http://localhost:8000/plan-crawl \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"destination\":\"Oakland\",\"duration\":\"3\",\"interests\":\"IPA\"}'"
echo ""
echo "🔑 To enable HERE API (if not already set):"
echo "   1. Get API key from https://platform.here.com/"
echo "   2. Add to backend/.env: HERE_API_KEY=your-key-here"
echo "   3. Restart server: kill $(lsof -ti:8000) && ./start.sh"
echo ""
echo "📚 Documentation:"
echo "   - Quick Start: QUICK_START_HERE_API.md"
echo "   - Full Guide: HERE_API_SETUP.md"
echo "   - Changes: CHANGES_HERE_API.md"
