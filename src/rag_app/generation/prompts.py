"""Prompt templates for grounded RAG generation."""

QUERY_ANALYSIS_PROMPT = """You are a retrieval query analyst.
Rewrite the request as a concise search query. Preserve names, dates, and constraints.
Return only the search query.

Request: {query}
"""

QUERY_REWRITE_PROMPT = """The search query below returned no usable evidence.
Produce one broader but semantically faithful search query. Return only the query.

Original request: {original_query}
Previous query: {retrieval_query}
"""

ANSWER_PROMPT = """Answer the request using only the supplied evidence.
Every factual claim must cite one or more evidence identifiers exactly like [C1].
Do not invent identifiers or facts. If the evidence is insufficient, reply exactly:
I cannot answer from the available evidence.

Request: {query}

Evidence:
{context}
"""

ABSTENTION_TEXT = "I cannot answer from the available evidence."
