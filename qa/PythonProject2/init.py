import documenttest
from vectorstore import*
def initial(knowledge):
    #连接数据库，并初始化vector
    database=knowledge
    vec=VectorStore(database)
    #初始化document类
    document_processor = documenttest.DocumentProcessor()
    #初始化chat类
    return vec, document_processor
