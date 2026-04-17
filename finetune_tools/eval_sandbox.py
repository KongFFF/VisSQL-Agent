import sqlite3

def execute_sql(db_path: str, sql_query: str):
    """
    执行器：在一个隔离的 SQLite 数据库中执行 SQL 语句。
    
    参数:
        db_path (str): .sqlite 数据库文件的绝对或相对路径
        sql_query (str): 需要执行的 SQL 语句
        
    返回:
        tuple: (执行状态: bool, 执行结果或错误信息: any)
               - 如果成功，返回 (True, 查询结果的列表，例如 [(1, 'Alice'), (2, 'Bob')])
               - 如果失败，返回 (False, 具体的错误字符串信息)
    """
    # 提前声明变量，防止在 finally 中引用未绑定的变量
    conn = None
    cursor = None

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)#通过数据库路径获取数据库连接对象

        cursor = conn.cursor()#获取游标对象

        cursor.execute(sql_query)#执行SQL语句
        result = cursor.fetchall()#这会把查询到的所有行，变成一个 Python 的列表（List），列表里每一行是一个元组（Tuple）
        
        return (True, result)
    
    except sqlite3.Error as e:
        return (False, str(e))
    # 如果捕获到 SQLite 特有的错误，把错误信息变成字符串

    except Exception as e:
        return (False, f"未知错误: {str(e)}")
    # 捕获其他意想不到的 Python 错误

    finally:
       # 无论 return 了什么，无论是否报错，退出函数前绝对会执行这里
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    pass