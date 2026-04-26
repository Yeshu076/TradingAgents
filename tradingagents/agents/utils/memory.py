"""Financial situation memory using BM25 for lexical similarity matching.

Uses BM25 (Best Matching 25) algorithm for retrieval - no API calls,
no token limits, works offline with any LLM provider.
"""

import os
import json
import re
from typing import List, Tuple
from rank_bm25 import BM25Okapi

class FinancialSituationMemory:
    """Memory system for storing and retrieving financial situations using persistent BM25."""

    def __init__(self, name: str, config: dict = None):
        """Initialize the memory system."""
        self.name = name
        
        self.db_dir = os.path.join(os.path.dirname(__file__), "..", "..", "dataflows", "persistent_memory")
        os.makedirs(self.db_dir, exist_ok=True)
        self.db_path = os.path.join(self.db_dir, f"{self.name}.json")
        
        self.documents: List[str] = []
        self.recommendations: List[dict] = []
        self.bm25 = None
        
        self._load()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\b\w+\b', text.lower())

    def _rebuild_index(self):
        if self.documents:
            tokenized_docs = [self._tokenize(doc) for doc in self.documents]    
            self.bm25 = BM25Okapi(tokenized_docs)
        else:
            self.bm25 = None

    def _save(self):
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump({"documents": self.documents, "recommendations": self.recommendations}, f, indent=4)

    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.documents = data.get("documents", [])
                    self.recommendations = data.get("recommendations", [])
                self._rebuild_index()
            except Exception:
                pass

    def add_situations(self, situations_and_advice: List[Tuple[str, str]], trade_outcomes: List[str] = None):     
        if not situations_and_advice:
            return
            
        for idx, (situation, recommendation) in enumerate(situations_and_advice):
            outcome = trade_outcomes[idx] if trade_outcomes else "Unknown outcome"
            self.documents.append(situation)
            self.recommendations.append({"recommendation": recommendation, "outcome": outcome})

        self._rebuild_index()
        self._save()

    def get_memories(self, current_situation: str, n_matches: int = 1) -> List[dict]:
        if not self.documents or self.bm25 is None:
            return []

        query_tokens = self._tokenize(current_situation)
        scores = self.bm25.get_scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_matches]

        matches = []
        max_score = max(scores) if max(scores) > 0 else 1
        
        for idx in top_indices:
            normalized_score = scores[idx] / max_score if max_score > 0 else 0
            rec_data = self.recommendations[idx]
            matches.append({
                "matched_situation": self.documents[idx],
                "recommendation": rec_data.get("recommendation", ""),
                "outcome": rec_data.get("outcome", ""),
                "similarity_score": normalized_score,
            })

        return matches

    def clear(self):
        """Clear all stored memories."""
        self.documents = []
        self.recommendations = []
        self.bm25 = None
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

if __name__ == "__main__":
    pass
