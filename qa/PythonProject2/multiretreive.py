import chromadb
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core import VectorStoreIndex, StorageContext
from rank_bm25 import BM25Okapi
import jieba
import numpy as np
from typing import Dict
from retreive_embed import *
api_key="c8235203fbf347e488b4c2527d3038b6.Rx1cYGdwhj2GHjYB"
embed_model=CustomEmbedding(api_key=api_key)
class ChromaHybridRetriever:
    """Chroma向量检索 + BM25关键词检索的混合检索器"""

    def __init__(self, chroma_path: str, collection_name: str = "default"):
        # 1. 连接Chroma数据库
        self.chroma_client = chromadb.PersistentClient(path=chroma_path)
        self.collection = self.chroma_client.get_or_create_collection(collection_name)

        # 2. 获取所有文档用于BM25
        all_data = self.collection.get(
            include=["documents", "metadatas"],
            limit=10000  # 根据实际情况调整
        )

        self.all_documents = all_data["documents"]  # 所有文档文本
        self.all_metadatas = all_data["metadatas"]  # 所有元数据

        # 3. 为BM25准备分词后的文档
        self.tokenized_docs = self._tokenize_documents(self.all_documents)
        self.bm25 = BM25Okapi(self.tokenized_docs)

        # 4. 初始化向量检索（使用LlamaIndex）
        vector_store = ChromaVectorStore(
            chroma_collection=self.collection
        )
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store
        )
        self.vector_index = VectorStoreIndex.from_vector_store(
            vector_store,
            storage_context=storage_context,
            embed_model=embed_model
        )

    def _tokenize_documents(self, documents: List[str]) -> List[List[str]]:
        """使用jieba分词文档，用于BM25"""
        tokenized = []
        for doc in documents:
            # 使用jieba分词（搜索引擎模式，更适合检索）
            tokens = list(jieba.cut_for_search(doc))

            # 可选：过滤停用词
            # tokens = self._filter_stopwords(tokens)

            # 过滤单字
            tokens = [t for t in tokens if len(t) > 1]

            tokenized.append(tokens)
        return tokenized

    def _filter_stopwords(self, tokens: List[str]) -> List[str]:
        """过滤中文停用词"""
        # 加载停用词表
        stopwords = set([
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到",
            "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这"
        ])
        return [t for t in tokens if t not in stopwords]

    def hybrid_search(self, query: str, top_k: int = 10, alpha: float = 0.5) -> List[Dict]:
        """
        混合检索：向量检索 + BM25关键词检索

        Args:
            query: 查询文本
            top_k: 返回结果数量
            alpha: 向量检索权重 (0-1)，BM25权重为 1-alpha
        """
        # 1. 向量检索（语义搜索）
        vector_results = self._vector_search(query, top_k * 2)

        # 2. BM25关键词检索
        bm25_results = self._bm25_search(query, top_k * 2)

        # 3. 融合结果
        fused_results = self._fuse_results(
            vector_results,
            bm25_results,
            top_k,
            alpha=alpha
        )

        return fused_results

    def _vector_search(self, query: str, top_k: int) -> List[Dict]:
        """执行向量检索"""
        retriever = self.vector_index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query)

        results = []
        for node in nodes:
            results.append({
                "id": node.node.node_id,
                "text": node.node.text,
                "metadata": node.node.metadata,
                "vector_score": node.score,
                "bm25_score": 0.0,  # 初始化为0
                "combined_score": 0.0
            })

        return results

    def _bm25_search(self, query: str, top_k: int) -> List[Dict]:
        """执行BM25关键词检索"""
        # 对查询进行分词
        query_tokens = list(jieba.cut_for_search(query))

        # 获取BM25分数
        scores = self.bm25.get_scores(query_tokens)

        # 获取top_k个结果
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:  # 只返回有分数的结果
                results.append({
                    "id": f"doc_{idx}",  # 使用文档索引作为ID
                    "text": self.all_documents[idx],
                    "metadata": self.all_metadatas[idx] if idx < len(self.all_metadatas) else {},
                    "vector_score": 0.0,  # 初始化为0
                    "bm25_score": float(scores[idx]),
                    "combined_score": 0.0
                })

        return results

    def _fuse_results(self, vector_results: List[Dict], bm25_results: List[Dict],
                      top_k: int, alpha: float = 0.5) -> List[Dict]:
        """
        融合向量检索和BM25检索的结果

        Args:
            alpha: 向量检索的权重 (0-1)
                    0.0 = 只用BM25
                    0.5 = 两者平等
                    1.0 = 只用向量
        """
        # 方法1：基于ID映射的加权分数融合
        all_results = {}

        # 处理向量检索结果
        for result in vector_results:
            doc_id = result["id"]
            if doc_id not in all_results:
                all_results[doc_id] = result
            all_results[doc_id]["vector_score"] = result["vector_score"]

        # 处理BM25检索结果（需要映射到相同的ID）
        for result in bm25_results:
            # 尝试找到对应的向量结果（基于文本内容相似度）
            matching_id = self._find_matching_id(result, vector_results)
            if matching_id:
                # 如果找到匹配，合并分数
                if matching_id in all_results:
                    all_results[matching_id]["bm25_score"] = result["bm25_score"]
                else:
                    result["vector_score"] = 0.0
                    all_results[matching_id] = result
            else:
                # 如果没有匹配，单独添加
                doc_id = result["id"]
                if doc_id not in all_results:
                    result["vector_score"] = 0.0
                    all_results[doc_id] = result

        # 计算综合分数并排序
        for doc_id, result in all_results.items():
            # 归一化分数（可选）
            # 简单加权平均
            result["combined_score"] = (
                    alpha * result.get("vector_score", 0) +
                    (1 - alpha) * result.get("bm25_score", 0)
            )

        # 按综合分数排序，返回top_k
        sorted_results = sorted(
            all_results.values(),
            key=lambda x: x["combined_score"],
            reverse=True
        )[:top_k]

        return sorted_results

    def _find_matching_id(self, bm25_result: Dict, vector_results: List[Dict]) -> str:
        """找到BM25结果对应的向量结果ID（基于文本相似度）"""
        bm25_text = bm25_result["text"]

        # 简单方法：完全匹配文本
        for vec_result in vector_results:
            if vec_result["text"] == bm25_text:
                return vec_result["id"]

        # 如果找不到完全匹配，可以尝试其他方法（如文本相似度）
        # 这里简化为返回None
        return None
