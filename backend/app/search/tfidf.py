"""Local TF-IDF implementation for Phase 7 Search & Similarity.

Pure Python, sparse-vector implementation.
No scikit-learn, no NumPy, no external ML dependencies.

Formulas
--------
TF(term, doc)  = count(term in doc) / len(doc_tokens)  (raw term frequency)
IDF(term)      = log((N + 1) / (df + 1)) + 1           (smoothed IDF)
TF-IDF(t,d)    = TF(t,d) * IDF(t)

Cosine similarity uses the sparse dot-product / (norm_A * norm_B).
"""

import math
from collections import Counter
from typing import Dict, List, Tuple

# Type aliases
SparseVector = Dict[str, float]   # {term: tfidf_weight}
TokenList = List[str]


# ---------------------------------------------------------------------------
# IDF
# ---------------------------------------------------------------------------

def build_idf(corpus_tokens: List[TokenList]) -> Dict[str, float]:
    """Compute smoothed IDF for every term in the corpus.

    IDF(term) = log((N + 1) / (df + 1)) + 1

    where N is the number of documents and df is the number of documents
    containing the term.

    Args:
        corpus_tokens: A list of token-lists, one per document.

    Returns:
        Dictionary mapping term → IDF value.
    """
    N = len(corpus_tokens)
    if N == 0:
        return {}

    df: Counter[str] = Counter()
    for tokens in corpus_tokens:
        # Document frequency counts each term once per document
        df.update(set(tokens))

    idf: Dict[str, float] = {}
    for term, doc_freq in df.items():
        idf[term] = math.log((N + 1) / (doc_freq + 1)) + 1.0

    return idf


# ---------------------------------------------------------------------------
# TF-IDF vector
# ---------------------------------------------------------------------------

def vectorize(tokens: TokenList, idf: Dict[str, float]) -> SparseVector:
    """Produce a sparse TF-IDF vector for a single token list.

    Only terms present in *idf* (i.e. in the training corpus) are included.
    Unknown query terms are silently ignored, which is the standard approach.

    Args:
        tokens: Token list for a single document or query.
        idf: Pre-computed IDF mapping from :func:`build_idf`.

    Returns:
        Sparse vector {term: tfidf_weight}.
    """
    if not tokens:
        return {}

    tf: Counter[str] = Counter(tokens)
    total = len(tokens)

    vec: SparseVector = {}
    for term, count in tf.items():
        if term in idf:
            vec[term] = (count / total) * idf[term]

    return vec


def vectorize_query(query_tokens: TokenList, idf: Dict[str, float]) -> SparseVector:
    """Vectorize a query using the corpus IDF.

    Identical to :func:`vectorize` — provided as a named alias for clarity.
    """
    return vectorize(query_tokens, idf)


# ---------------------------------------------------------------------------
# Cosine similarity (sparse)
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: SparseVector, vec_b: SparseVector) -> float:
    """Compute cosine similarity between two sparse TF-IDF vectors.

    Returns 0.0 for empty vectors, zero-norm vectors, or no shared terms.
    Result is guaranteed in [0.0, 1.0] (non-negative TF-IDF weights).
    Never returns NaN or infinity.

    Args:
        vec_a: First sparse vector.
        vec_b: Second sparse vector.

    Returns:
        Cosine similarity in [0.0, 1.0].
    """
    if not vec_a or not vec_b:
        return 0.0

    # Dot product — iterate over the smaller vector for efficiency
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a

    dot = sum(weight * vec_b.get(term, 0.0) for term, weight in vec_a.items())

    if dot == 0.0:
        return 0.0

    norm_a = math.sqrt(sum(w * w for w in vec_a.values()))
    norm_b = math.sqrt(sum(w * w for w in vec_b.values()))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    result = dot / (norm_a * norm_b)
    # Clamp to [0.0, 1.0] to guard against floating-point rounding
    return min(1.0, max(0.0, result))


# ---------------------------------------------------------------------------
# Corpus index
# ---------------------------------------------------------------------------

class CorpusIndex:
    """Lightweight in-memory TF-IDF index for a single scan.

    Builds IDF from all documents and produces query vectors on demand.

    Attributes:
        doc_vectors: Sparse TF-IDF vectors keyed by document ID.
        idf: IDF mapping.
        doc_token_lists: Raw token lists by document ID (for debug/inspection).
    """

    def __init__(self) -> None:
        self.doc_vectors: Dict[int, SparseVector] = {}
        self.idf: Dict[str, float] = {}
        self.doc_token_lists: Dict[int, TokenList] = {}

    def build(self, documents: Dict[int, TokenList]) -> None:
        """Build the IDF table and TF-IDF vectors for all documents.

        Args:
            documents: Mapping of document_id → token list.
        """
        if not documents:
            return

        self.doc_token_lists = dict(documents)
        corpus = list(documents.values())
        self.idf = build_idf(corpus)

        self.doc_vectors = {
            doc_id: vectorize(tokens, self.idf)
            for doc_id, tokens in documents.items()
        }

    def query(self, query_tokens: TokenList) -> List[Tuple[int, float]]:
        """Score all indexed documents against a query.

        Args:
            query_tokens: Tokenized query.

        Returns:
            List of (doc_id, cosine_similarity) sorted by similarity descending,
            then by doc_id ascending for deterministic tie-breaking.
        """
        if not query_tokens or not self.doc_vectors:
            return []

        q_vec = vectorize_query(query_tokens, self.idf)
        if not q_vec:
            return []

        scores: List[Tuple[int, float]] = [
            (doc_id, cosine_similarity(q_vec, doc_vec))
            for doc_id, doc_vec in self.doc_vectors.items()
        ]

        # Sort: similarity descending, then doc_id ascending (deterministic)
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores
