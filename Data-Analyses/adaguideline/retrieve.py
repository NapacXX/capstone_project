"""
Loads the FAISS index built by build_index.py and retrieves the
top-k guideline chunks for each vignette. Reads vignettes from a CSV
(case_id, query_text) and writes one row per retrieved chunk to a
CSV that the R pipeline can join back onto vignette data.

    python retrieve.py --vignettes vignettes.csv --out retrieved_context.csv --k 5

vignettes.csv needs at least: case_id, query_text
query_text can be the raw vignette text, or a short string built
from the patient's structured features (e.g. "eGFR 24, heart
failure present, obesity, on metformin and sitagliptin") - the
latter tends to retrieve more precisely since the KB chunks are
short and clinically specific.
"""

import argparse
import csv

from llama_index.core import StorageContext, load_index_from_storage
from llama_index.core.settings import Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.faiss import FaissVectorStore

INDEX_DIR = "ada_index"
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def load_index():
    Settings.embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)
    vector_store = FaissVectorStore.from_persist_dir(INDEX_DIR)
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store, persist_dir=INDEX_DIR
    )
    return load_index_from_storage(storage_context)


def retrieve_for_vignettes(vignettes_path, out_path, k):
    index = load_index()
    retriever = index.as_retriever(similarity_top_k=k)

    with open(vignettes_path, newline="", encoding="utf-8") as f_in, open(
        out_path, "w", newline="", encoding="utf-8"
    ) as f_out:
        reader = csv.DictReader(f_in)
        writer = csv.writer(f_out)
        writer.writerow(
            ["case_id", "rank", "layer", "chunk_id", "score", "retrieved_text"]
        )

        for row in reader:
            case_id = row["case_id"]
            nodes = retriever.retrieve(row["query_text"])
            for rank, node in enumerate(nodes, start=1):
                meta = node.node.metadata
                chunk_id = meta.get("chunk_id") or meta.get("rule_id") or ""
                writer.writerow(
                    [
                        case_id,
                        rank,
                        meta.get("layer", ""),
                        chunk_id,
                        round(node.score, 4) if node.score is not None else "",
                        node.node.get_content().replace("\n", " "),
                    ]
                )

    print(f"retrieved context for vignettes in {vignettes_path} -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vignettes", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    retrieve_for_vignettes(args.vignettes, args.out, args.k)
