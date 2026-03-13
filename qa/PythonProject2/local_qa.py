from init import *
from vectorstore import *
knowledge = "D:\qa\database"#可自行修改
#在local对各个模块的函数进行整合处理
# 初始化
vec,document_processor= initial(knowledge)

def delete_collection(collection_name):
    vec.delete_collection(collection_name)

def list_collection():  # 我觉得这个可以直接显示到webui上z
    client = chromadb.PersistentClient('D:\qa\database')
    collections = client.list_collections()
    name=[]
    for collection in collections:
        name.append(f"collection {collection}")
    return  name

def create_collections(collection_name=''):
        vec.create_collection(collection_name)
##这个添加的结果是
def add_file_to_collection(file_paths, collection_name):
    # 步骤1：统一输入格式（保持不变）
    if isinstance(file_paths, str):
        file_paths = [file_paths]

    # 步骤2：调用process_file批量处理所有文件
    document, failed_files = document_processor.process_file(file_paths)
    if not document:  # 若没有有效文件
        return f"所有文件处理失败：\n" + "\n".join(failed_files)
    split_docs = document_processor.text_split(document)  # 这里是关键！
    try:
        vec.add_document(split_docs, collection_name)  # 假设vec接受纯文本列表
        success_count = len(file_paths) - len(failed_files)
        return f"添加结果：成功{success_count}个，失败{len(failed_files)}个\n失败详情：\n" + "\n".join(failed_files)
    except Exception as p:
            return f"❌ 数据库添加失败：{str(p)}"

