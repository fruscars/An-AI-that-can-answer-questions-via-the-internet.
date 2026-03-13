from typing import List
from langchain_core.documents import Document as LangchainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PDFMinerLoader, Docx2txtLoader, UnstructuredImageLoader
import os
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(self, chunk_size: int = 200, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # 优化分隔符：优先按大语义分割，贴合中文书籍逻辑
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", " "]
        )

    def _load_pdf(self, file_path) -> List[LangchainDocument]:
        pdf_loader = PDFMinerLoader(file_path)
        documents = pdf_loader.load()
        # 给每页加独立元数据（页码关键！）
        for idx, doc in enumerate(documents):
            doc.metadata.update({
                "source": file_path,
                "type": "PDF",
                "page": idx + 1,  # 每页单独页码，不是总页数
                "page_count": len(documents)
            })
        return documents

    def _load_docx(self, file_path) -> List[LangchainDocument]:
        docx_loader = Docx2txtLoader(file_path)
        documents = docx_loader.load()
        for idx, doc in enumerate(documents):
            doc.metadata.update({
                "source": file_path,
                "type": "docx",
                "page": idx + 1,
                "page_count": len(documents)
            })
        return documents

    def _load_image(self, file_path) -> List[LangchainDocument]:
        image_loader = UnstructuredImageLoader(file_path)
        documents = image_loader.load()
        for doc in documents:
            doc.metadata.update({
                "source": file_path,
                "type": "image"
            })
        return documents

    # 核心优化：单文档独立切，保留元数据，不合并！
    def text_split(self, documents: List[LangchainDocument]):
        split_docs = []
        # 逐文档切分，不合并，保留每页/每段的原始上下文
        for doc in documents:
            # 跳过空文本
            if not doc.page_content.strip():
                continue
            # 对单个文档切块，继承原元数据
            splits = self.text_splitter.split_documents([doc])
            # 给每个切块加唯一标识，方便溯源
            for idx, split in enumerate(splits):
                split.metadata["chunk_id"] = f"{split.metadata.get('page', 1)}_{idx}"
                split_docs.append(split)

        logger.info(f"切割完成，共生成 {len(split_docs)} 个文本块")
        # 返回带完整元数据的文档对象，不是纯文本
        return split_docs

    def get_file_type(self, file_path:str):
        name, ext = os.path.splitext(file_path)
        ext = ext.lower()
        if ext in ['.pdf']:
            return 'pdf'
        elif ext in ['.doc', '.docx']:
            return 'docx'
        elif ext in ['.jpg', '.jpeg', '.png', '.gif']:
            return 'image'
        else:
            return 'unknown'

    def process_file(self, file_paths:list[str]): #整合处理注意返回的是列表
        #检查是否含有file
        if not file_paths:
            return "错误：未提供任何文件路径"
        #初始化数据
        results = []
        document: List[LangchainDocument] = []  # 定义为单个Document的列表
        # 2. 循环处理每个文件
        for idx, fp in enumerate(file_paths):
            # 2.1 单文件校验,并切割
            if not os.path.exists(fp):
                results.append(f"❌ 文件不存在 → {fp}")
                continue
            file_type=self.get_file_type(file_path=fp)
            if file_type == 'pdf':
                document.extend(self._load_pdf(file_path=fp))
            elif file_type == 'docx':
                document.extend(self._load_docx(file_path=fp))
            elif file_type == 'image':
                document.extend(self._load_image(fp))
            else:
                results.append( f"❌ 不支持的文件类型 → {fp}")
        return document, results



