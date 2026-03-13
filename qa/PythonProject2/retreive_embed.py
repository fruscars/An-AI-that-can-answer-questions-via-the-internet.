import logging
import requests
import json
from llama_index.core.embeddings import BaseEmbedding
from typing import List
from pydantic import Field
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
class CustomEmbedding(BaseEmbedding):
    """自定义智谱AI Embedding类"""

    # 必须在这里声明所有字段，使用Field
    api_key: str = Field(..., description="智谱AI API密钥")
    base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/embeddings", description="API基础URL")
    model: str = Field(default="embedding-3", description="使用的模型")
    headers: dict = Field(default_factory=dict, description="请求头")

    def __init__(self, api_key: str, model: str = "embedding-3", **kwargs):
        """初始化自定义Embedding

        Args:
            api_key: 智谱AI API密钥
            model: 使用的模型名称，默认为embedding-3
            **kwargs: 其他传递给父类的参数
        """
        # 设置headers
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        # 使用super().__init__初始化所有字段
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://open.bigmodel.cn/api/paas/v4/embeddings",
            headers=headers,
            **kwargs
        )

    def _get_embedding(self, text: str) -> List[float]:
        """内部方法：获取单个文本的embedding"""
        if not text or not text.strip():
            return []

        data = {
            "model": self.model,
            "input": text.strip()
        }

        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=data,
                timeout=30
            )
            response.raise_for_status()

            result = response.json()

            if "data" in result and result["data"]:
                embedding = result["data"][0]["embedding"]
                return embedding

            logger.error(f"API响应格式错误: {result}")
            return []

        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败: {e}")
            return []
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.error(f"解析失败: {e}")
            return []

    def _get_text_embedding(self, text: str) -> List[float]:
        """
        获取单个文本的embedding（LlamaIndex内部调用）

        Args:
            text: 输入文本

        Returns:
            embedding向量列表
        """
        return self._get_embedding(text)

    def _get_query_embedding(self, query: str) -> List[float]:
        """
        获取查询的embedding（LlamaIndex内部调用）

        Args:
            query: 查询文本

        Returns:
            embedding向量列表
        """
        return self._get_embedding(query)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        批量获取文本的embeddings

        Args:
            texts: 文本列表

        Returns:
            embedding向量列表的列表
        """
        if not texts:
            return []

        # 过滤空文本
        valid_texts = [text.strip() for text in texts if text and text.strip()]
        if not valid_texts:
            return []

        data = {
            "model": self.model,
            "input": valid_texts
        }

        try:
            logger.info(f"正在批量请求智谱AI Embedding API，模型: {self.model}，文本数量: {len(valid_texts)}")

            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=data,
                timeout=60
            )
            response.raise_for_status()

            result = response.json()

            if "data" not in result:
                logger.error(f"API响应格式错误: {result}")
                return []

            # 提取所有embedding
            embeddings = []
            for item in result["data"]:
                if "embedding" in item:
                    embeddings.append(item["embedding"])
                else:
                    logger.warning(f"缺少embedding字段: {item}")
                    embeddings.append([])

            # 打印使用情况
            if "usage" in result:
                usage = result["usage"]
                logger.info(f"Tokens使用情况: {usage}")

            logger.info(f"成功生成 {len(embeddings)} 个嵌入向量")
            if embeddings and embeddings[0]:
                logger.info(f"向量维度: {len(embeddings[0])}")

            return embeddings

        except requests.exceptions.RequestException as e:
            logger.error(f"批量请求失败: {e}")
            return []
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"批量解析失败: {e}")
            return []

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """异步获取单个文本的embedding"""
        return self._get_embedding(text)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """异步获取查询的embedding"""
        return self._get_embedding(query)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """异步批量获取文本的embeddings"""
        return self._get_text_embeddings(texts)

    @classmethod
    def class_name(cls) -> str:
        """返回类名，用于序列化"""
        return "CustomEmbedding"


# 或者使用更简洁的配置方式（推荐）
class CustomEmbeddingSimple(BaseEmbedding):
    """简化版自定义智谱AI Embedding类"""

    # 必须在这里声明所有字段
    api_key: str
    model: str = "embedding-3"

    class Config:
        """Pydantic配置"""
        arbitrary_types_allowed = True

    def __init__(self, api_key: str, model: str = "embedding-3", **kwargs):
        """初始化"""
        super().__init__(
            api_key=api_key,
            model=model,
            **kwargs
        )

        # 初始化其他属性（不是Pydantic字段）
        self._base_url = "https://open.bigmodel.cn/api/paas/v4/embeddings"
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def _get_embedding(self, text: str) -> List[float]:
        """获取单个文本的embedding"""
        if not text or not text.strip():
            return []

        data = {
            "model": self.model,
            "input": text.strip()
        }

        try:
            response = requests.post(
                self._base_url,
                headers=self._headers,
                json=data,
                timeout=30
            )
            response.raise_for_status()

            result = response.json()

            if "data" in result and result["data"]:
                return result["data"][0]["embedding"]

            logger.error(f"API响应格式错误: {result}")
            return []

        except Exception as e:
            logger.error(f"请求失败: {e}")
            return []

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._get_embedding(text)

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._get_embedding(query)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量获取文本的embeddings"""
        if not texts:
            return []

        valid_texts = [text.strip() for text in texts if text and text.strip()]
        if not valid_texts:
            return []

        data = {
            "model": self.model,
            "input": valid_texts
        }

        try:
            response = requests.post(
                self._base_url,
                headers=self._headers,
                json=data,
                timeout=60
            )
            response.raise_for_status()

            result = response.json()

            if "data" in result:
                return [item.get("embedding", []) for item in result["data"]]

            return []

        except Exception as e:
            logger.error(f"批量请求失败: {e}")
            return []

    async def _aget_text_embedding(self, text: str) -> List[float]:
        return self._get_embedding(text)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return self._get_embedding(query)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._get_text_embeddings(texts)

