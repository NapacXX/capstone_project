"""
Builds a FAISS-backed vector index from the ADA 2026 knowledge base
(kb_data.py). Run once to create the index, then reuse it from
retrieve.py for each vignette.

    python build_index.py
"""

import faiss
from llama_index.core import Document, VectorStoreIndex, StorageContext
from llama_index.core.settings import Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.faiss import FaissVectorStore

from kb_data import TEXT_CHUNKS, MEDICATION_TABLE, RULE_REGISTRY

INDEX_DIR = "ada_index"
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"  # local, no API key needed
EMBED_DIM = 384


def text_chunk_documents():
    docs = []
    for c in TEXT_CHUNKS:
        docs.append(
            Document(
                text=c["text"],
                metadata={
                    "layer": "text_chunk",
                    "chunk_id": c["chunk_id"],
                    "recommendation_id": c["recommendation_id"],
                    "source_page": c["source_page"],
                    "condition": c["condition"],
                    "drug_class": c["drug_class"],
                    "action_type": c["action_type"],
                    "rule_strength": c["rule_strength"],
                },
            )
        )
    return docs


def medication_table_documents():
    docs = []
    for row in MEDICATION_TABLE:
        text = (
            f"{row['drug_class']}: glycemic efficacy {row['glycemic_efficacy']}, "
            f"hypoglycemia risk {row['hypoglycemia_risk']}, weight effect {row['weight_effect']}, "
            f"heart failure effect {row['heart_failure_effect']}, "
            f"CKD progression effect {row['ckd_progression_effect']}. "
            f"Kidney dosing: {row['kidney_dosing_consideration']}. "
            f"Major cautions: {row['major_cautions']}."
        )
        docs.append(
            Document(
                text=text,
                metadata={
                    "layer": "medication_table",
                    "drug_class": row["drug_class"],
                },
            )
        )
    return docs


def rule_registry_documents():
    docs = []
    for r in RULE_REGISTRY:
        text = (
            f"Rule {r['rule_id']}: if {r['trigger_logic']}, then for {r['drug_class']} "
            f"the expected action is {r['expected_action']} ({r['expected_dose_label']}), "
            f"rule strength {r['rule_strength']}."
        )
        docs.append(
            Document(
                text=text,
                metadata={
                    "layer": "rule_registry",
                    "rule_id": r["rule_id"],
                    "drug_class": r["drug_class"],
                    "rule_strength": r["rule_strength"],
                    "supporting_chunk_ids": r["supporting_chunk_ids"],
                },
            )
        )
    return docs


def build():
    Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)

    documents = (
        text_chunk_documents()
        + medication_table_documents()
        + rule_registry_documents()
    )

    faiss_index = faiss.IndexFlatL2(EMBED_DIM)
    vector_store = FaissVectorStore(faiss_index=faiss_index)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_documents(
        documents, storage_context=storage_context
    )
    index.storage_context.persist(persist_dir=INDEX_DIR)
    print(f"indexed {len(documents)} chunks into {INDEX_DIR}")


if __name__ == "__main__":
    build()
