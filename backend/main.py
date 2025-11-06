from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import time
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

# Minimal observability via Phoenix/OpenInference (optional)
try:
    from phoenix.otel import register
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from openinference.instrumentation.litellm import LiteLLMInstrumentor
    from openinference.instrumentation import using_prompt_template, using_metadata, using_attributes
    from opentelemetry import trace
    _TRACING = True
except Exception:
    def using_prompt_template(**kwargs):  # type: ignore
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield
        return _noop()
    def using_metadata(*args, **kwargs):  # type: ignore
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield
        return _noop()
    def using_attributes(*args, **kwargs):  # type: ignore
        from contextlib import contextmanager
        @contextmanager
        def _noop():
            yield
        return _noop()
    _TRACING = False

# LangGraph + LangChain
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict, Annotated
import operator
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import InMemoryVectorStore
import httpx

# HuggingFace embeddings for open-source alternative
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    _HF_AVAILABLE = True
except ImportError:
    _HF_AVAILABLE = False


class CrawlRequest(BaseModel):
    destination: str
    duration: str
    budget: Optional[str] = None
    interests: Optional[str] = None
    travel_style: Optional[str] = None
    # Optional fields for enhanced session tracking and observability
    user_input: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    turn_index: Optional[int] = None


class CrawlResponse(BaseModel):
    result: str
    tool_calls: List[Dict[str, Any]] = []


def _init_llm():
    # Simple, test-friendly LLM init
    class _Fake:
        def __init__(self):
            pass
        def bind_tools(self, tools):
            return self
        def invoke(self, messages):
            class _Msg:
                content = "Test itinerary"
                tool_calls: List[Dict[str, Any]] = []
            return _Msg()

    if os.getenv("TEST_MODE"):
        return _Fake()
    if os.getenv("OPENAI_API_KEY"):
        return ChatOpenAI(model="gpt-3.5-turbo", temperature=0.7, max_tokens=1500)
    elif os.getenv("OPENROUTER_API_KEY"):
        # Use OpenRouter via OpenAI-compatible client
        return ChatOpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            temperature=0.7,
        )
    else:
        # Require a key unless running tests
        raise ValueError("Please set OPENAI_API_KEY or OPENROUTER_API_KEY in your .env")


# Initialize LLM lazily to avoid import-time errors
llm = None

def get_llm():
    global llm
    if llm is None:
        llm = _init_llm()
    return llm


# Feature flag for optional RAG demo (opt-in for learning)
ENABLE_RAG = os.getenv("ENABLE_RAG", "0").lower() not in {"0", "false", "no"}


# RAG helper: Load curated local guides as LangChain documents
def _load_local_documents(path: Path) -> List[Document]:
    """Load local guides JSON and convert to LangChain Documents."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return []

    docs: List[Document] = []
    for row in raw:
        description = row.get("description")
        city = row.get("city")
        if not description or not city:
            continue
        interests = row.get("interests", []) or []
        metadata = {
            "city": city,
            "interests": interests,
            "source": row.get("source"),
        }
        # Prefix city + interests in content so embeddings capture location context
        interest_text = ", ".join(interests) if interests else "general travel"
        content = f"City: {city}\nInterests: {interest_text}\nGuide: {description}"
        docs.append(Document(page_content=content, metadata=metadata))
    return docs


class LocalGuideRetriever:
    """Retrieves curated local experiences using vector similarity search.
    
    This class demonstrates production RAG patterns for students:
    - Vector embeddings for semantic search
    - Fallback to keyword matching when embeddings unavailable
    - Graceful degradation with feature flags
    """
    
    def __init__(self, data_path: Path):
        """Initialize retriever with local guides data.
        
        Args:
            data_path: Path to local_guides.json file
        """
        self._docs = _load_local_documents(data_path)
        self._embeddings = None
        self._vectorstore: Optional[InMemoryVectorStore] = None
        
        # Only create embeddings when RAG is enabled and we have docs
        if ENABLE_RAG and self._docs and not os.getenv("TEST_MODE"):
            try:
                # Try HuggingFace embeddings first (free, open-source)
                use_hf = os.getenv("USE_HUGGINGFACE_EMBEDDINGS", "1").lower() in {"1", "true", "yes"}
                
                if use_hf and _HF_AVAILABLE:
                    # Use lightweight, fast model for MVP
                    model_name = os.getenv("HUGGINGFACE_EMBED_MODEL", "all-MiniLM-L6-v2")
                    print(f"🤗 Loading HuggingFace embeddings: {model_name}")
                    self._embeddings = HuggingFaceEmbeddings(
                        model_name=model_name,
                        model_kwargs={'device': 'cpu'},
                        encode_kwargs={'normalize_embeddings': True}
                    )
                    print(f"✅ HuggingFace embeddings loaded successfully")
                elif os.getenv("OPENAI_API_KEY"):
                    # Fall back to OpenAI if configured
                    model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
                    print(f"🔑 Using OpenAI embeddings: {model}")
                    self._embeddings = OpenAIEmbeddings(model=model)
                else:
                    print("⚠️  No embeddings available, using keyword fallback")
                    self._embeddings = None
                
                # Create vector store if we have embeddings
                if self._embeddings:
                    store = InMemoryVectorStore(embedding=self._embeddings)
                    print(f"📚 Indexing {len(self._docs)} documents...")
                    store.add_documents(self._docs)
                    self._vectorstore = store
                    print(f"✅ Vector store ready with {len(self._docs)} documents")
                    
            except Exception as e:
                # Gracefully degrade to keyword search if embeddings fail
                print(f"⚠️  Embedding initialization failed: {e}")
                print("   Falling back to keyword search")
                self._embeddings = None
                self._vectorstore = None

    @property
    def is_empty(self) -> bool:
        """Check if any documents were loaded."""
        return not self._docs

    def retrieve(self, destination: str, interests: Optional[str], *, k: int = 3) -> List[Dict[str, Any]]:
        """Retrieve top-k relevant local guides for a destination.
        
        Args:
            destination: City or destination name
            interests: Comma-separated interests (e.g., "food, art")
            k: Number of results to return
            
        Returns:
            List of dicts with 'content', 'metadata', and 'score' keys
        """
        if not ENABLE_RAG or self.is_empty:
            return []

        # Use vector search if available, otherwise fall back to keywords
        if not self._vectorstore:
            return self._keyword_fallback(destination, interests, k=k)

        query = destination
        if interests:
            query = f"{destination} with interests {interests}"
        
        try:
            # LangChain retriever ensures embeddings + searches are traced
            retriever = self._vectorstore.as_retriever(search_kwargs={"k": max(k, 4)})
            docs = retriever.invoke(query)
        except Exception:
            return self._keyword_fallback(destination, interests, k=k)

        # Format results with metadata and scores
        top_docs = docs[:k]
        results = []
        for doc in top_docs:
            score_val: float = 0.0
            if isinstance(doc.metadata, dict):
                maybe_score = doc.metadata.get("score")
                if isinstance(maybe_score, (int, float)):
                    score_val = float(maybe_score)
            results.append({
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": score_val,
            })

        if not results:
            return self._keyword_fallback(destination, interests, k=k)
        return results

    def _keyword_fallback(self, destination: str, interests: Optional[str], *, k: int) -> List[Dict[str, Any]]:
        """Simple keyword-based retrieval when embeddings unavailable.
        
        This demonstrates graceful degradation for students learning about
        fallback strategies in production systems.
        """
        dest_lower = destination.lower()
        interest_terms = [part.strip().lower() for part in (interests or "").split(",") if part.strip()]

        def _score(doc: Document) -> int:
            score = 0
            city_match = doc.metadata.get("city", "").lower()
            # Match city name
            if dest_lower and dest_lower.split(",")[0] in city_match:
                score += 2
            # Match interests
            for term in interest_terms:
                if term and term in " ".join(doc.metadata.get("interests") or []).lower():
                    score += 1
                if term and term in doc.page_content.lower():
                    score += 1
            return score

        scored_docs = [(_score(doc), doc) for doc in self._docs]
        scored_docs.sort(key=lambda item: item[0], reverse=True)
        top_docs = scored_docs[:k]
        
        results = []
        for score, doc in top_docs:
            if score > 0:
                results.append({
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                    "score": float(score),
                })
        return results


# Initialize retriever at module level (loads data once at startup)
_DATA_DIR = Path(__file__).parent / "data"
GUIDE_RETRIEVER = LocalGuideRetriever(_DATA_DIR / "local_guides.json")


# Search API configuration and helpers
SEARCH_TIMEOUT = 10.0  # seconds


def _compact(text: str, limit: int = 800) -> str:
    """Compact text to a maximum length, truncating at word boundaries.
    
    Increased to 800 chars to capture brewery names, addresses, and details.
    """
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(",.;- ")


def _here_api_search_breweries(city: str, max_results: int = 10) -> Optional[str]:
    """Search for breweries using HERE Places API with verified addresses and details.
    
    Returns structured brewery information including:
    - Name and full address
    - Phone numbers and website
    - Categories and opening hours (if available)
    - Distance from city center
    """
    here_key = os.getenv("HERE_API_KEY")
    if not here_key or here_key == "your-here-api-key-here":
        return None
    
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT) as client:
            # First, geocode the city to get coordinates
            geocode_resp = client.get(
                "https://geocode.search.hereapi.com/v1/geocode",
                params={
                    "q": city,
                    "apiKey": here_key,
                    "limit": 1,
                }
            )
            geocode_resp.raise_for_status()
            geocode_data = geocode_resp.json()
            
            if not geocode_data.get("items"):
                return None
            
            location = geocode_data["items"][0]["position"]
            lat, lng = location["lat"], location["lng"]
            
            # Use HERE Discover API instead of Browse for better brewery results
            # Try different search queries to find breweries
            search_queries = [
                f"brewery {city}",
                f"craft beer {city}",
                f"brewpub {city}",
            ]
            all_results = []
            
            for query in search_queries:
                try:
                    discover_resp = client.get(
                        "https://discover.search.hereapi.com/v1/discover",
                        params={
                            "at": f"{lat},{lng}",
                            "q": query,
                            "limit": 20,  # Get more results to filter
                            "apiKey": here_key,
                        }
                    )
                    discover_resp.raise_for_status()
                    discover_data = discover_resp.json()
                    all_results.extend(discover_data.get("items", []))
                except Exception:
                    continue  # Try next search query
            
            if not all_results:
                return None
            
            # Deduplicate by ID and format results
            # Filter to only include brewery-related places
            seen_ids = set()
            breweries = []
            
            for item in all_results:
                place_id = item.get("id")
                if place_id in seen_ids:
                    continue
                
                name = item.get("title", "")
                
                # Filter: only include if name contains brewery-related terms
                name_lower = name.lower()
                if not any(term in name_lower for term in ["brew", "tap", "ale", "beer", "hop"]):
                    continue
                    
                seen_ids.add(place_id)
                address_obj = item.get("address", {})
                address_parts = [
                    address_obj.get("street", ""),
                    address_obj.get("city", ""),
                    address_obj.get("stateCode", ""),
                    address_obj.get("postalCode", ""),
                ]
                address = ", ".join([p for p in address_parts if p])
                
                # Get additional details
                contacts = item.get("contacts", [{}])[0] if item.get("contacts") else {}
                phone = contacts.get("phone", [{}])[0].get("value", "") if contacts.get("phone") else ""
                website = contacts.get("www", [{}])[0].get("value", "") if contacts.get("www") else ""
                
                categories = ", ".join([cat.get("name", "") for cat in item.get("categories", [])])
                
                # Opening hours
                hours = ""
                if item.get("openingHours"):
                    hours_text = item["openingHours"][0].get("text", [])
                    if hours_text:
                        hours = "; ".join(hours_text[:3])  # First 3 days
                
                brewery_info = f"{name}"
                if address:
                    brewery_info += f"\nAddress: {address}"
                if phone:
                    brewery_info += f"\nPhone: {phone}"
                if website:
                    brewery_info += f"\nWebsite: {website}"
                if categories:
                    brewery_info += f"\nType: {categories}"
                if hours:
                    brewery_info += f"\nHours: {hours}"
                
                breweries.append(brewery_info)
                
                if len(breweries) >= max_results:
                    break
            
            if not breweries:
                return None
            
            result = f"Found {len(breweries)} breweries/beer bars in {city}:\n\n" + "\n\n".join(breweries)
            return _compact(result, limit=2000)  # More space for detailed venue info
            
    except Exception as e:
        # Fail gracefully and fall back to other search methods
        return None


def _search_api(query: str) -> Optional[str]:
    """Search the web using Tavily or SerpAPI if configured, return None otherwise.
    
    This demonstrates graceful degradation: tools work with or without API keys.
    Students can enable real search by adding TAVILY_API_KEY or SERPAPI_API_KEY.
    """
    query = query.strip()
    if not query:
        return None

    # Try Tavily first (recommended for AI apps)
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            with httpx.Client(timeout=SEARCH_TIMEOUT) as client:
                resp = client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key,
                        "query": query,
                        "max_results": 5,  # More results for better brewery coverage
                        "search_depth": "advanced",  # Get more detailed info including addresses
                        "include_answer": True,
                        "include_raw_content": False,
                        "include_domains": [],
                        "exclude_domains": ["yelp.com"],  # Yelp often has outdated closure info
                        "topic": "general",  # Use general for local business searches
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                answer = data.get("answer") or ""
                snippets = [
                    item.get("content") or item.get("snippet") or ""
                    for item in data.get("results", [])
                ]
                combined = " ".join([answer] + snippets).strip()
                if combined:
                    return _compact(combined, limit=1200)  # More space for multiple breweries with addresses
        except Exception:
            pass  # Fail gracefully, try next option

    # Try SerpAPI as fallback
    serp_key = os.getenv("SERPAPI_API_KEY")
    if serp_key:
        try:
            with httpx.Client(timeout=SEARCH_TIMEOUT) as client:
                resp = client.get(
                    "https://serpapi.com/search",
                    params={
                        "api_key": serp_key,
                        "engine": "google",
                        "num": 5,
                        "q": query,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                organic = data.get("organic_results", [])
                snippets = [item.get("snippet", "") for item in organic]
                combined = " ".join(snippets).strip()
                if combined:
                    return _compact(combined)
        except Exception:
            pass  # Fail gracefully

    return None  # No search APIs configured


def _llm_fallback(instruction: str, context: Optional[str] = None) -> str:
    """Use the LLM to generate a response when search APIs aren't available.
    
    This ensures tools always return useful information, even without API keys.
    """
    prompt = "Respond with 200 characters or less.\n" + instruction.strip()
    if context:
        prompt += "\nContext:\n" + context.strip()
    response = get_llm().invoke([
        SystemMessage(content="You are a concise travel assistant."),
        HumanMessage(content=prompt),
    ])
    return _compact(response.content)


def _with_prefix(prefix: str, summary: str) -> str:
    """Add a prefix to a summary for clarity."""
    text = f"{prefix}: {summary}" if prefix else summary
    return _compact(text)


# Tools with real API calls + LLM fallback (graceful degradation pattern)
@tool
def essential_info(destination: str) -> str:
    """Return essential local brewery scene info: popular neighborhoods for breweries, transit options, parking, and best times for crawls."""
    query = f"{destination} craft beer scene brewery neighborhoods beer bars transit parking best time to visit breweries"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} brewery scene", summary)
    
    # LLM fallback when no search API is configured
    instruction = f"Describe the craft brewery scene in {destination}: popular brewery neighborhoods, how to get around, parking availability, and best times for a brewery crawl."
    return _llm_fallback(instruction)


@tool
def budget_basics(destination: str, duration: str) -> str:
    """Return brewery crawl budget: typical beer prices, flight costs, food prices, and transportation between stops."""
    query = f"{destination} craft beer prices brewery costs pint prices beer flight costs food prices"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} brewery costs", summary)
    
    instruction = f"Estimate costs for a {duration}-stop brewery crawl in {destination}: beer prices, flights, food, and transportation."
    return _llm_fallback(instruction)


@tool
def local_flavor(destination: str, interests: Optional[str] = None) -> str:
    """Find CURRENTLY OPEN craft breweries and beer bars with verified addresses and hours."""
    focus = interests or "craft beer"
    
    # Try HERE API first - best source for verified addresses and current info
    here_results = _here_api_search_breweries(destination, max_results=8)
    if here_results:
        return _with_prefix(f"{destination} breweries (verified addresses)", here_results)
    
    # Fall back to web search if HERE API unavailable
    current_year = datetime.now().year
    query = f"{destination} craft breweries open {current_year} currently operating beer bars {focus} addresses hours still in business"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} open breweries", summary)
    
    # Final fallback to LLM
    instruction = f"List 5-8 craft breweries and beer bars in {destination} that are CURRENTLY OPEN and operating in {current_year}, with actual street addresses and specialties for {focus}. Do not include closed breweries."
    return _llm_fallback(instruction)


@tool
def day_plan(destination: str, day: int) -> str:
    """Return a simple day plan outline for a specific day number."""
    query = f"{destination} day {day} itinerary highlights"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"Day {day} in {destination}", summary)
    
    instruction = f"Outline key activities for day {day} in {destination}, covering morning, afternoon, and evening."
    return _llm_fallback(instruction)


# Additional simple tools per agent (to mirror original multi-tool behavior)
@tool
def weather_brief(destination: str) -> str:
    """Return current weather for outdoor brewery/beer garden planning."""
    query = f"{destination} current weather today forecast outdoor seating"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} weather", summary)
    
    instruction = f"Give today's weather for {destination} focusing on conditions for outdoor brewery seating and walking between venues."
    return _llm_fallback(instruction)


@tool
def visa_brief(destination: str) -> str:
    """Return a brief visa guidance for travel planning."""
    query = f"{destination} tourist visa requirements entry rules"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} visa", summary)
    
    instruction = f"Provide a visa guidance summary for visiting {destination}, including advice to confirm with the relevant embassy."
    return _llm_fallback(instruction)


@tool
def attraction_prices(destination: str, attractions: Optional[List[str]] = None) -> str:
    """Return pricing information for attractions."""
    items = attractions or ["popular attractions"]
    focus = ", ".join(items)
    query = f"{destination} attraction ticket prices {focus}"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} attraction prices", summary)
    
    instruction = f"Share typical ticket prices and savings tips for attractions such as {focus} in {destination}."
    return _llm_fallback(instruction)


@tool
def local_customs(destination: str) -> str:
    """Return cultural etiquette and customs information."""
    query = f"{destination} cultural etiquette travel customs"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} customs", summary)
    
    instruction = f"Summarize key etiquette and cultural customs travelers should know before visiting {destination}."
    return _llm_fallback(instruction)


@tool
def hidden_gems(destination: str) -> str:
    """Return lesser-known OPEN breweries and beer bars with addresses."""
    # Try HERE API first for comprehensive list, then filter in prompt
    here_results = _here_api_search_breweries(destination, max_results=10)
    if here_results:
        return _with_prefix(f"{destination} breweries (all verified locations)", here_results)
    
    # Fall back to web search
    current_year = datetime.now().year
    query = f"{destination} hidden gem breweries open {current_year} lesser known craft beer bars currently operating neighborhood breweries addresses"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} hidden breweries", summary)
    
    # Final fallback to LLM
    instruction = f"List lesser-known craft breweries or neighborhood beer bars in {destination} that are CURRENTLY OPEN in {current_year}, with addresses. Focus on hidden gems, not mainstream venues."
    return _llm_fallback(instruction)


@tool
def travel_time(from_location: str, to_location: str, mode: str = "public") -> str:
    """Return travel time estimates between locations."""
    query = f"travel time {from_location} to {to_location} by {mode}"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{from_location}→{to_location} {mode}", summary)
    
    instruction = f"Estimate travel time from {from_location} to {to_location} by {mode} transport."
    return _llm_fallback(instruction)


@tool
def packing_list(destination: str, duration: str, activities: Optional[List[str]] = None) -> str:
    """Return packing recommendations for the trip."""
    acts = ", ".join(activities or ["sightseeing"])
    query = f"what to pack for {destination} {duration} {acts}"
    summary = _search_api(query)
    if summary:
        return _with_prefix(f"{destination} packing", summary)
    
    instruction = f"Suggest packing essentials for a {duration} trip to {destination} focused on {acts}."
    return _llm_fallback(instruction)


class CrawlState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    crawl_request: Dict[str, Any]
    research: Optional[str]
    budget: Optional[str]
    local: Optional[str]
    final: Optional[str]
    tool_calls: Annotated[List[Dict[str, Any]], operator.add]


def research_agent(state: CrawlState) -> CrawlState:
    req = state["crawl_request"]
    destination = req["destination"]
    prompt_t = (
        "You are a research assistant for local brewery crawl planning.\n"
        "Gather information about the craft beer scene in {destination}.\n"
        "Focus on: neighborhood vibe, brewery concentration, transit options, and best times to visit.\n"
        "Use tools to get local scene info and current weather, then provide a brief summary."
    )
    vars_ = {"destination": destination}
    
    messages = [SystemMessage(content=prompt_t.format(**vars_))]
    tools = [essential_info, weather_brief]  # Removed visa_brief - not needed for local crawls
    agent = get_llm().bind_tools(tools)
    
    calls: List[Dict[str, Any]] = []
    tool_results = []
    
    # Agent metadata and prompt template instrumentation
    with using_attributes(tags=["research", "info_gathering"]):
        if _TRACING:
            current_span = trace.get_current_span()
            if current_span:
                current_span.set_attribute("metadata.agent_type", "research")
                current_span.set_attribute("metadata.agent_node", "research_agent")
        
        with using_prompt_template(template=prompt_t, variables=vars_, version="v1"):
            res = agent.invoke(messages)
    
    # Collect tool calls and execute them
    if getattr(res, "tool_calls", None):
        for c in res.tool_calls:
            calls.append({"agent": "research", "tool": c["name"], "args": c.get("args", {})})
        
        tool_node = ToolNode(tools)
        tr = tool_node.invoke({"messages": [res]})
        tool_results = tr["messages"]
        
        # Add tool results to conversation and ask LLM to synthesize
        messages.append(res)
        messages.extend(tool_results)
        
        synthesis_prompt = "Based on the above information, provide a comprehensive summary for the traveler."
        messages.append(SystemMessage(content=synthesis_prompt))
        
        # Instrument synthesis LLM call with its own prompt template
        synthesis_vars = {"destination": destination, "context": "tool_results"}
        with using_prompt_template(template=synthesis_prompt, variables=synthesis_vars, version="v1-synthesis"):
            final_res = get_llm().invoke(messages)
        out = final_res.content
    else:
        out = res.content

    return {"messages": [SystemMessage(content=out)], "research": out, "tool_calls": calls}


def budget_agent(state: CrawlState) -> CrawlState:
    req = state["crawl_request"]
    destination, duration = req["destination"], req["duration"]
    budget = req.get("budget", "moderate")
    prompt_t = (
        "You are a budget analyst for brewery crawl planning.\n"
        "Estimate costs for a {duration}-stop crawl in {destination} with budget level: {budget}.\n"
        "Focus on: per-brewery costs (beers, flights, food), transportation between stops.\n"
        "Provide cost per stop and total estimated crawl cost. Do NOT include hotel/accommodation costs."
    )
    vars_ = {"destination": destination, "duration": duration, "budget": budget}
    
    messages = [SystemMessage(content=prompt_t.format(**vars_))]
    tools = [budget_basics, attraction_prices]
    agent = get_llm().bind_tools(tools)
    
    calls: List[Dict[str, Any]] = []
    
    # Agent metadata and prompt template instrumentation
    with using_attributes(tags=["budget", "cost_analysis"]):
        if _TRACING:
            current_span = trace.get_current_span()
            if current_span:
                current_span.set_attribute("metadata.agent_type", "budget")
                current_span.set_attribute("metadata.agent_node", "budget_agent")
        
        with using_prompt_template(template=prompt_t, variables=vars_, version="v1"):
            res = agent.invoke(messages)
    
    if getattr(res, "tool_calls", None):
        for c in res.tool_calls:
            calls.append({"agent": "budget", "tool": c["name"], "args": c.get("args", {})})
        
        tool_node = ToolNode(tools)
        tr = tool_node.invoke({"messages": [res]})
        
        # Add tool results and ask for synthesis
        messages.append(res)
        messages.extend(tr["messages"])
        
        synthesis_prompt = f"Create a detailed crawl budget breakdown for {duration} in {destination} with a {budget} budget."
        messages.append(SystemMessage(content=synthesis_prompt))
        
        # Instrument synthesis LLM call
        synthesis_vars = {"duration": duration, "destination": destination, "budget": budget}
        with using_prompt_template(template=synthesis_prompt, variables=synthesis_vars, version="v1-synthesis"):
            final_res = get_llm().invoke(messages)
        out = final_res.content
    else:
        out = res.content

    return {"messages": [SystemMessage(content=out)], "budget": out, "tool_calls": calls}


def local_agent(state: CrawlState) -> CrawlState:
    req = state["crawl_request"]
    destination = req["destination"]
    interests = req.get("interests", "local culture")
    travel_style = req.get("travel_style", "standard")
    
    # RAG: Retrieve curated local guides if enabled (only for exact city matches)
    context_lines = []
    if ENABLE_RAG:
        retrieved = GUIDE_RETRIEVER.retrieve(destination, interests, k=5)
        # Filter to only include exact city matches
        filtered = [r for r in retrieved if r["metadata"].get("city", "").lower() == destination.lower()]
        
        if filtered:
            context_lines.append(f"=== {destination} Breweries from Curated Database ===")
            for idx, item in enumerate(filtered, 1):
                content = item["content"]
                meta = item["metadata"]
                venue = meta.get("venue_name", "")
                address = meta.get("address", "")
                
                if venue and address:
                    context_lines.append(f"{idx}. {venue}")
                    context_lines.append(f"   Address: {address}")
                    context_lines.append(f"   {content}")
                else:
                    context_lines.append(f"{idx}. {content}")
                context_lines.append(f"   Source: {meta.get('source', 'Unknown')}")
            context_lines.append(f"=== End of Database ===\n")
            context_lines.append(f"Use venues above if available for {destination}. Otherwise, use venues from web search tools.")
        else:
            context_lines.append(f"=== No Database Entries for {destination} ===")
            context_lines.append(f"Rely entirely on web search tools (local_flavor, hidden_gems) to find breweries in {destination}.")
    
    context_text = "\n".join(context_lines) if context_lines else ""
    
    prompt_t = (
        "You are a local guide for brew crawl planning.\n"
        "Find craft breweries and bars in {destination} for someone interested in: {interests}.\n"
        "Crawl style: {travel_style}. Use tools to gather local brewery and bar insights.\n"
    )
    
    # Add retrieved context to prompt if available
    if context_text:
        prompt_t += "\nRelevant curated experiences from our database:\n{context}\n"
    
    vars_ = {
        "destination": destination,
        "interests": interests,
        "travel_style": travel_style,
        "context": context_text if context_text else "No curated context available.",
    }
    
    messages = [SystemMessage(content=prompt_t.format(**vars_))]
    tools = [local_flavor, local_customs, hidden_gems]
    agent = get_llm().bind_tools(tools)
    
    calls: List[Dict[str, Any]] = []
    
    # Agent metadata and prompt template instrumentation
    with using_attributes(tags=["local", "local_experiences"]):
        if _TRACING:
            current_span = trace.get_current_span()
            if current_span:
                current_span.set_attribute("metadata.agent_type", "local")
                current_span.set_attribute("metadata.agent_node", "local_agent")
                if ENABLE_RAG and context_text:
                    current_span.set_attribute("metadata.rag_enabled", "true")
        
        with using_prompt_template(template=prompt_t, variables=vars_, version="v1"):
            res = agent.invoke(messages)
    
    if getattr(res, "tool_calls", None):
        for c in res.tool_calls:
            calls.append({"agent": "local", "tool": c["name"], "args": c.get("args", {})})
        
        tool_node = ToolNode(tools)
        tr = tool_node.invoke({"messages": [res]})
        
        # Add tool results and ask for synthesis
        messages.append(res)
        messages.extend(tr["messages"])
        
        synthesis_prompt = f"""Create a curated list of craft breweries and bars for someone interested in {interests} with a {travel_style} approach.

CRITICAL: For EACH brewery, include:
- Full venue name (exactly as shown in database)
- Complete street address (exactly as shown in database)  
- Brief description of beer styles and atmosphere
Format each entry clearly so the itinerary agent can use the exact addresses."""
        messages.append(SystemMessage(content=synthesis_prompt))
        
        # Instrument synthesis LLM call
        synthesis_vars = {"interests": interests, "travel_style": travel_style, "destination": destination}
        with using_prompt_template(template=synthesis_prompt, variables=synthesis_vars, version="v1-synthesis"):
            final_res = get_llm().invoke(messages)
        out = final_res.content
    else:
        out = res.content

    return {"messages": [SystemMessage(content=out)], "local": out, "tool_calls": calls}


def itinerary_agent(state: CrawlState) -> CrawlState:
    req = state["crawl_request"]
    destination = req["destination"]
    duration = req["duration"]
    travel_style = req.get("travel_style", "standard")
    user_input = (req.get("user_input") or "").strip()
    
    prompt_parts = [
        "You are creating a BREWERY CRAWL itinerary for {destination}.",
        "This is a single-day crawl visiting {duration} different brewery/beer bar STOPS.",
        "",
        "CRITICAL VENUE SELECTION RULES:",
        "- ONLY use venues explicitly listed in the 'Local Breweries' section below",
        "- Each venue MUST be CURRENTLY OPEN and operating (not closed or out of business)",
        "- Each venue MUST be located in {destination}",
        "- DO NOT invent breweries or use venues from other cities",
        "- Copy venue names and addresses EXACTLY as shown in Local Breweries data",
        "- If a venue's status is unclear, note it in the itinerary with 'Verify hours before visiting'",
        "",
        "FORMATTING RULES:",
        "- Use 'Stop 1', 'Stop 2', 'Stop 3' etc. (NOT 'Day 1', 'Day 2')",
        "- Format: Venue Name, Address, Beer Highlights, Why Visit",
        "- Include walking/travel time between stops",
        "- Estimated time at each stop: 60-90 minutes",
        "- Do NOT mention breakfast, lunch, dinner, or departure",
        "",
        "Context from agents:",
        "Research: {research}",
        "Budget: {budget}",
        "",
        "Local Breweries: {local}",
    ]
    if user_input:
        prompt_parts.append("User preferences: {user_input}")
    
    prompt_parts.extend([
        "",
        "Create a logical route that minimizes walking distance and follows a natural geographic flow.",
        "Only include venues mentioned in the Local agent data above - do NOT invent breweries."
    ])
    
    prompt_t = "\n".join(prompt_parts)
    vars_ = {
        "duration": duration,
        "destination": destination,
        "travel_style": travel_style,
        "research": (state.get("research") or "")[:400],
        "budget": (state.get("budget") or "")[:400],
        "local": (state.get("local") or "")[:800],  # More context for venue names
        "user_input": user_input,
    }
    
    # Add span attributes for better observability in Arize
    # NOTE: using_attributes must be OUTER context for proper propagation
    with using_attributes(tags=["itinerary", "final_agent"]):
        if _TRACING:
            current_span = trace.get_current_span()
            if current_span:
                current_span.set_attribute("metadata.itinerary", "true")
                current_span.set_attribute("metadata.agent_type", "itinerary")
                current_span.set_attribute("metadata.agent_node", "itinerary_agent")
                if user_input:
                    current_span.set_attribute("metadata.user_input", user_input)
        
        # Prompt template wrapper for Arize Playground integration
        with using_prompt_template(template=prompt_t, variables=vars_, version="v1"):
            res = get_llm().invoke([SystemMessage(content=prompt_t.format(**vars_))])
    
    return {"messages": [SystemMessage(content=res.content)], "final": res.content}


def build_graph():
    g = StateGraph(CrawlState)
    g.add_node("research_node", research_agent)
    g.add_node("budget_node", budget_agent)
    g.add_node("local_node", local_agent)
    g.add_node("itinerary_node", itinerary_agent)

    # Run research, budget, and local agents in parallel
    g.add_edge(START, "research_node")
    g.add_edge(START, "budget_node")
    g.add_edge(START, "local_node")
    
    # All three agents feed into the itinerary agent
    g.add_edge("research_node", "itinerary_node")
    g.add_edge("budget_node", "itinerary_node")
    g.add_edge("local_node", "itinerary_node")
    
    g.add_edge("itinerary_node", END)

    # Compile without checkpointer to avoid state persistence issues
    return g.compile()


app = FastAPI(title="Brew Crawl Planner")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for images
backend_dir = Path(__file__).resolve().parent
# Since we're running from root directory, frontend is at ./frontend/
images_dir = backend_dir.parent / "frontend" / "images"

# Debug: Log the paths for troubleshooting
print(f"Backend dir: {backend_dir}")
print(f"Images dir: {images_dir}")
print(f"Images dir exists: {images_dir.exists()}")

if images_dir.exists():
    app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")
    print(f"Mounted images from: {images_dir}")
else:
    print(f"Images directory not found at: {images_dir}")
    # Try alternative path structure for Render
    alt_images_dir = backend_dir / "frontend" / "images"
    if alt_images_dir.exists():
        app.mount("/images", StaticFiles(directory=str(alt_images_dir)), name="images")
        print(f"Mounted images from alternative path: {alt_images_dir}")
    else:
        print(f"Alternative images directory also not found: {alt_images_dir}")


@app.get("/")
def serve_frontend():
    # Use Path for robust path resolution
    backend_dir = Path(__file__).resolve().parent
    frontend_path = backend_dir.parent / "frontend" / "index.html"
    if frontend_path.exists():
        return FileResponse(str(frontend_path))
    return {"message": f"frontend/index.html not found at {frontend_path}", "backend_dir": str(backend_dir), "frontend_path": str(frontend_path)}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "brew-crawl-planner"}


# Initialize tracing once at startup, not per request
if _TRACING:
    try:
        # Phoenix Cloud uses PHOENIX_API_KEY and PHOENIX_COLLECTOR_ENDPOINT
        # These are automatically picked up by the register() function
        phoenix_api_key = os.getenv("PHOENIX_API_KEY")
        phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        
        if phoenix_api_key and phoenix_endpoint:
            # Register with Phoenix Cloud - it will use env vars automatically
            tp = register(project_name="brew-crawl-planner")
            LangChainInstrumentor().instrument(tracer_provider=tp, include_chains=True, include_agents=True, include_tools=True)
            LiteLLMInstrumentor().instrument(tracer_provider=tp, skip_dep_check=True)
            print("✅ Phoenix tracing initialized successfully")
            print(f"   Endpoint: {phoenix_endpoint}")
        else:
            print("⚠️  Phoenix tracing not configured - missing PHOENIX_API_KEY or PHOENIX_COLLECTOR_ENDPOINT")
            print("   Set these in your .env file to enable tracing")
    except Exception as e:
        print(f"❌ Phoenix tracing initialization failed: {e}")
        import traceback
        traceback.print_exc()

@app.post("/plan-crawl", response_model=CrawlResponse)
def plan_crawl(req: CrawlRequest):
    graph = build_graph()
    
    # Only include necessary fields in initial state
    # Agent outputs (research, budget, local, final) will be added during execution
    state = {
        "messages": [],
        "crawl_request": req.model_dump(),
        "tool_calls": [],
    }
    
    # Add session and user tracking attributes to the trace
    session_id = req.session_id
    user_id = req.user_id
    turn_idx = req.turn_index
    
    # Build attributes for session and user tracking
    attrs_kwargs = {}
    if session_id:
        attrs_kwargs["session_id"] = session_id
    if user_id:
        attrs_kwargs["user_id"] = user_id
    
    # Add turn_index as a custom span attribute if provided
    if turn_idx is not None and _TRACING:
        with using_attributes(**attrs_kwargs):
            current_span = trace.get_current_span()
            if current_span:
                current_span.set_attribute("turn_index", turn_idx)
            out = graph.invoke(state)
    else:
        with using_attributes(**attrs_kwargs):
            out = graph.invoke(state)
    
    return CrawlResponse(result=out.get("final", ""), tool_calls=out.get("tool_calls", []))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
