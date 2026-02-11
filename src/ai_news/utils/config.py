"""AI News 환경 설정 및 상수 정의"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """애플리케이션 설정"""

    # AWS
    s3_bucket: str = field(default_factory=lambda: os.environ.get("S3_BUCKET", "gov-funding-monitor"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "ap-northeast-2"))

    # Slack
    slack_bot_token: str = field(default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", ""))
    slack_channel_id: str = field(default_factory=lambda: os.environ.get("AI_NEWS_CHANNEL_ID", ""))

    # Bedrock
    bedrock_region: str = field(default_factory=lambda: os.environ.get("BEDROCK_REGION", "us-west-2"))
    bedrock_model_id: str = field(default_factory=lambda: os.environ.get(
        "BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1"
    ))

    # Filtering
    importance_threshold: float = field(default_factory=lambda: float(
        os.environ.get("AI_NEWS_IMPORTANCE_THRESHOLD", "0.5")
    ))
    max_articles_per_source: int = field(default_factory=lambda: int(
        os.environ.get("AI_NEWS_MAX_PER_SOURCE", "20")
    ))


# AI 키워드 필터 (Hacker News 등에서 관련 기사 선별)
AI_KEYWORDS = [
    # 모델/기술
    "LLM", "GPT", "Claude", "Gemini", "Llama", "Mistral", "Phi",
    "transformer", "diffusion", "multimodal",
    # 분야
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "NLP", "natural language", "computer vision", "OCR",
    "generative", "foundation model", "language model",
    # 회사/조직
    "OpenAI", "Anthropic", "DeepMind", "Google AI", "Meta AI",
    "Hugging Face", "Stability AI", "Midjourney",
    # 기술 용어
    "neural network", "reinforcement learning", "RLHF", "fine-tuning",
    "RAG", "retrieval augmented", "embedding", "tokenizer",
    "benchmark", "reasoning", "agent", "agentic",
    # 검색/문서 처리 (회사 핵심 분야)
    "retrieval", "vector search", "vector database", "semantic search",
    "knowledge base", "information retrieval", "document",
    "chunking", "reranking", "re-ranking",
    "optical character", "document processing", "text extraction",
    # 벡터/임베딩 도구
    "FAISS", "Pinecone", "Weaviate", "Milvus", "ChromaDB", "Chroma",
    "LangChain", "LlamaIndex", "LangGraph",
    # 한국어 키워드
    "인공지능", "딥러닝", "머신러닝", "자연어처리", "생성형",
    "벡터", "검색", "문서", "임베딩",
    # 추가 기술 용어
    "fine tuning", "prompt engineering", "context window",
    "inference", "quantization", "distillation",
    "vision language", "VLM", "MLOps", "LLMOps",
]

# arXiv 카테고리
ARXIV_CATEGORIES = [
    "cs.AI",   # Artificial Intelligence
    "cs.CL",   # Computation and Language (NLP)
    "cs.CV",   # Computer Vision
    "cs.LG",   # Machine Learning
]

# 크롤링 소스 URL
CRAWL_SOURCES = {
    "arxiv": {
        "name": "arXiv",
        "feeds": {cat: f"https://export.arxiv.org/rss/{cat}" for cat in ARXIV_CATEGORIES},
    },
    "hackernews": {
        "name": "Hacker News",
        "top_url": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "best_url": "https://hacker-news.firebaseio.com/v0/beststories.json",
        "item_url": "https://hacker-news.firebaseio.com/v0/item/{id}.json",
    },
    "techcrunch": {
        "name": "TechCrunch",
        "feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/",
    },
    "anthropic": {
        "name": "Anthropic",
        "news_url": "https://www.anthropic.com/news",
        "research_url": "https://www.anthropic.com/research",
    },
    "openai": {
        "name": "OpenAI",
        "blog_url": "https://openai.com/index/",
    },
    "deepmind": {
        "name": "Google DeepMind",
        "blog_url": "https://deepmind.google/discover/blog/",
    },
    "huggingface": {
        "name": "Hugging Face",
        "papers_url": "https://huggingface.co/papers",
    },
    "aitimes": {
        "name": "AI Times",
        "url": "https://www.aitimes.com/",
    },
    "itworld": {
        "name": "ITWorld Korea",
        "feed_url": "https://www.itworld.co.kr/rss/",
    },
    "etnews": {
        "name": "ETNews",
        "url": "https://www.etnews.com/",
    },
    "itdaily": {
        "name": "IT Daily",
        "url": "http://www.itdaily.kr/",
    },
    "aws_blog": {
        "name": "AWS ML Blog",
        "feed_url": "https://aws.amazon.com/blogs/machine-learning/feed/",
    },
    "azure_blog": {
        "name": "Azure Blog",
        "feed_url": "https://azure.microsoft.com/en-us/blog/feed/",
    },
    "google_blog": {
        "name": "Google AI Blog",
        "feed_url": "https://blog.google/technology/ai/rss/",
    },
    "ms_research": {
        "name": "Microsoft Research",
        "feed_url": "https://www.microsoft.com/en-us/research/feed/",
    },
    "google_research": {
        "name": "Google Research",
        "feed_url": "https://research.google/blog/rss/",
    },
    "medium": {
        "name": "Medium AI",
        "feed_url": "https://medium.com/feed/tag/artificial-intelligence",
    },
}


def get_config() -> Config:
    """설정 인스턴스 반환"""
    return Config()
