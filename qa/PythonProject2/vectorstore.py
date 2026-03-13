import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
from embedding_module import *
from multiretreive import *
from typing import List,Optional
from langchain_core.documents import Document as LangchainDocument
from config import *
logging.basicConfig(
    level=logging.INFO,  # ✅ 关键：设置为INFO（显示INFO及以上级别日志）
    format="%(asctime)s - %(levelname)s - %(message)s"  # 可选：自定义格式
)
logger = logging.getLogger(__name__)
class MyEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        logger.info(f"=== Embedding函数被触发 ===")
        logger.info(f"输入文本数量: {len(input)}")
        logger.info(f"第一条文本预览: {input[0][:50]}..." if input else "无输入")
        # 调用你的Embedding模型（确保e.embeddings正确实现）
        embeddings = e.embeddings(input)
        logger.info(f"生成向量数量: {len(embeddings)}")
        logger.info(f"第一条向量维度: {len(embeddings[0])}" if embeddings else "无向量")
        return embeddings
class VectorStore:
    def __init__(self,vector_path):
        self.client = chromadb.PersistentClient(path=vector_path)
        self.collection=None

    def add_document(self, split_docs: list[LangchainDocument], collection_name: str):
        collection = self.select_collection_name(collection_name)
        if not collection:
            logger.error(f"集合{collection_name}不存在且创建失败")
            return
        batch_size = 50
        total_docs = len(split_docs)
        logger.info(f"开始添加文档：共{total_docs}条，批次大小{batch_size}")
        # 2. 分批次处理
        for batch_idx in range(0, total_docs, batch_size):
            # 截取当前批次的文档
            batch_docs = split_docs[batch_idx: batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1  # 批次编号（从1开始）
            batch_len = len(batch_docs)

            try:
                # 3. 提取文本、元数据和ID
                texts = [doc.page_content for doc in batch_docs]
                metadatas = [doc.metadata for doc in batch_docs]
                # 生成唯一ID：用批次编号+文档在批次内的索引（确保全局不重复）
                ids = [f"doc_{batch_idx + i}" for i in range(batch_len)]  # 如doc_0、doc_1...doc_99（第1批）

                # 4. 批量添加到ChromaDB
                collection.add(
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids
                )
                logger.info(f"批次{batch_num}添加成功：{batch_len}条文档（ID范围：{ids[0]}~{ids[-1]}）")

            except Exception as e:
                logger.error(f"批次{batch_num}添加失败：{str(e)}，跳过该批次")
                continue  # 可根据需求改为重试逻辑

        logger.info(f"所有批次处理完成：共尝试添加{total_docs}条文档")
#优化对于没有答案的我觉得有必要使用agent,就是在知识库没有找到答案，提示转变为知识库外搜索
    def vector_search(self, query: str, collection_name: str="default"):
        path=KNOWLEDGE_PATH
        retreiver=ChromaHybridRetriever(path,collection_name)
        results=retreiver.hybrid_search(query)
        texts=[doc["text"] for doc in results]
        logger.info(results)
        return texts
    def create_collection(self, collection_name):
        """集合不存在则创建，存在则复用，均输出提示"""
        # 1. 检查集合是否存在
        existing_collections = [coll.name for coll in self.client.list_collections()]

        if collection_name in existing_collections:
            # 情况1：已存在 → 复用，输出提示
            self.collection = self.client.get_collection(
                name=collection_name,
                embedding_function=MyEmbeddingFunction()
            )
            print(f"✅ 集合 '{collection_name}' 已存在，已复用")  # 仅输出提示（无return值）
        else:
            # 情况2：不存在 → 创建，输出提示
            self.collection = self.client.create_collection(
                name=collection_name,
                embedding_function=MyEmbeddingFunction()
            )
            print(f"🆕 集合 '{collection_name}' 已成功创建")
    def delete_collection(self, collection_name):
        """删除集合：若存在则删除，不存在则返回提示（不报错）"""
        # 1. 先检查集合是否存在
        existing_collections = [coll.name for coll in self.client.list_collections()]

        if collection_name in existing_collections:
            # 情况1：集合已存在 → 执行删除
            self.client.delete_collection(name=collection_name)
            print( f"🗑️ 集合 '{collection_name}' 已成功删除")
        else:
            # 情况2：集合不存在 → 直接返回提示（不执行删除操作）
            print(f"⚠️ 集合 '{collection_name}' 不存在，无需删除")

    def is_empty(self):
        """判断知识库中是否没有任何集合"""
        # 调用chromadb的list_collections()获取所有集合
        collections = self.client.list_collections()
        if len(collections) == 0:
            return True
        else:
            return False

    def select_collection_name(self, collection_name):
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=MyEmbeddingFunction()
        )
        return self.collection  # 同时返回给外部使用


    #对document的处理
    def get_collection_documents(self, collection_name: str, limit: Optional[int] = None) -> Dict:
        """
        获取collection中的所有文档
        Args:
            collection_name: 知识库名称（可以是原始名称或实际名称）
            limit: 返回数量限制，None表示不限制（获取全部）

        Returns:
            包含documents, metadatas, ids的字典
        """
        logger.debug(f"获取文档: 传入名称={collection_name}, 实际名称={collection_name}")

        try:
            collection = self.client.get_collection(name=collection_name)

            # 获取总文档数量，用于日志
            total_count = collection.count()

            # 如果limit为None，使用总数量（获取全部）
            if limit is None:
                limit = total_count
                logger.info(f"Collection {collection_name} 总文档数: {total_count}, limit=None（获取全部）")
            else:
                logger.info(f"Collection {collection_name} 总文档数: {total_count}, limit: {limit}")
                # 如果limit小于总数量，记录警告
                if limit < total_count:
                    logger.warning(
                        f"limit ({limit}) 小于总文档数 ({total_count})，可能会截断结果。建议使用 limit=None 或 limit={total_count}")

            results = collection.get(limit=limit)
            retrieved_count = len(results.get('ids', []))
            logger.info(f"成功获取文档: collection={collection_name}, 返回文档数量={retrieved_count}/{total_count}")
            #返回特征
            return {
                'documents': results['documents'],
                'metadatas': results['metadatas'],
                'ids': results['ids']
            }
        except Exception as a:
            logger.error(f"获取文档失败: collection_name={collection_name}, actual_name={collection_name}, error={a}")
            raise ValueError(f"知识库不存在: {collection_name}")

    def delete_documents(self, collection_name: str, ids: List[str]) -> bool:
        """
        删除指定ID的文档

        Args:
            collection_name: 知识库名称（可以是原始名称或实际名称）
            ids: 要删除的文档ID列表

        Returns:
            是否删除成功
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            collection.delete(ids=ids)
            logger.info(f"从 {collection_name} 删除 {len(ids)} 个文档")
            return True
        except Exception as a:
            logger.error(f"删除文档失败: {a}")
            return False


    def get_document_count(self, collection_name: str) -> int:
        """
        获取知识库中的文档数量

        Args:
            collection_name: 知识库名称

        Returns:
            文档数量
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            return collection.count()
        except Exception:
            return 0
    def set_collection_dimension(self, collection_name: str, dimension: int) -> bool:
        """
        在collection的metadata中存储维度信息
        注意：Chroma可能不支持直接设置collection级别的metadata
        这个方法主要用于在文档metadata中存储维度信息

        Args:
            collection_name: 知识库名称
            dimension: 向量维度

        Returns:
            是否设置成功
        """
        # 由于Chroma的限制，维度信息主要存储在文档的metadata中
        # 这里可以添加一些辅助逻辑，比如创建一个特殊的metadata文档
        # 或者仅用于验证目的
        logger.info(f"记录collection {collection_name} 的维度: {dimension}")
        return True


