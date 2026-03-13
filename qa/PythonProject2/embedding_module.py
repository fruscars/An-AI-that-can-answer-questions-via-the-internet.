import numpy as np
import logging
import requests
import json
from typing import List
from config import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

class Embedding:
    def __init__(self, api_key: str):
        """
        初始化Embedding类

        Args:
            api_key: 智谱AI API密钥
        """
        self.api_key = api_key
        self.device = "api"
        self.base_url = "https://open.bigmodel.cn/api/paas/v4/embeddings"
        self.model = "embedding-3"
        # 设置请求头
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """
        生成文本的嵌入向量

        Args:
            texts: 要编码的文本列表

        Returns:
            嵌入向量列表，每个文本对应一个numpy数组
        """
        if not texts:
            return []

        # 使用指定模型或默认模型
        model_name =self.model

        # 准备请求数据
        data = {
            "model": model_name,
            "input": texts
        }

        try:
            logger.info(f"正在请求智谱AI Embedding API，模型: {model_name}，文本数量: {len(texts)}")

            # 发送POST请求
            response = requests.post(
                self.base_url,
                headers=self.headers,
                data=json.dumps(data),
                timeout=30
            )

            # 检查响应状态
            response.raise_for_status()

            # 解析响应
            result = response.json()

            if "data" not in result:
                logger.error(f"API响应格式错误: {result}")
                return []

            # 提取嵌入向量并转换为numpy数组
            embeddings = []
            for item in result["data"]:
                if "embedding" in item:
                    embeddings.append(np.array(item["embedding"], dtype=np.float32))
                else:
                    logger.warning(f"缺少嵌入向量: {item}")
                    embeddings.append(np.array([]))
            # 打印使用情况（如果有）
            if "usage" in result:
                usage = result["usage"]
                logger.info(f"Tokens使用情况: {usage}")

            logger.info(f"成功生成 {len(embeddings)} 个嵌入向量")
            logger.info(f"原维度: {len(embeddings[0])}" if embeddings else "无向量")
            return  embeddings

        except requests.exceptions.RequestException as w:
            logger.error(f"请求失败: {w}")
            return []
        except json.JSONDecodeError as w:
            logger.error(f"JSON解析失败: {w}")
            return []
        except Exception as w:
            logger.error(f"发生未知错误: {w}")
            return []

    def embed_single(self, text: str) -> np.ndarray:
        """
        生成单个文本的嵌入向量

        Args:
            text: 要编码的文本

        Returns:
            嵌入向量numpy数组
        """
        embeddings = self.embeddings([text])
        return embeddings[0] if embeddings else np.array([])

    def batch_embed(self, texts: List[str], batch_size: int = 10) -> List[np.ndarray]:
        """
        批量生成文本的嵌入向量（处理长列表）

        Args:
            texts: 要编码的文本列表
            batch_size: 每批处理的数量
        Returns:
            嵌入向量列表
        """
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            logger.info(f"处理批次 {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1}")

            embeddings = self.embeddings(batch)
            all_embeddings.extend(embeddings)

            # 避免请求过于频繁，可以添加延迟
            # time.sleep(0.1)

        return all_embeddings

    def get_embedding_dimension(self,embeddings: List[np.ndarray]) -> int:
        """
        获取嵌入列表的统一维度（仅当所有嵌入维度一致时返回，否则抛出异常）

        Args:
            embeddings: 嵌入向量列表（每个元素为1D numpy数组）

        Returns:
            int: 所有嵌入的统一维度

        Raises:
            ValueError: 嵌入列表为空、存在非1D数组或维度不一致
        """
        if not embeddings:
            raise ValueError("嵌入列表不能为空")
        # 检查第一个嵌入的维度
        first_dim = len(embeddings[0])
        if embeddings[0].ndim != 1:
            raise ValueError("嵌入必须是1D numpy数组")

        # 验证所有嵌入维度一致
        for emb in embeddings[1:]:
            if emb.ndim != 1 or len(emb) != first_dim:
                raise ValueError("所有嵌入必须是相同维度的1D numpy数组")
        logger.info(f"成功获取维度: {len(embeddings[0])}" if embeddings else "无向量")
        return first_dim

e=Embedding(api_key=EMBED_API_KEY)
