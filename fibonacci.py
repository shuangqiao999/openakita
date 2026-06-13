def fibonacci(n):
    """
    计算斐波那契数列的第 n 项
    
    参数:
        n: 要计算的项数（从 0 开始）
    
    返回:
        第 n 项的值
    
    示例:
        >>> fibonacci(0)
        0
        >>> fibonacci(1)
        1
        >>> fibonacci(10)
        55
    """
    if n < 0:
        raise ValueError("n 必须是非负整数")
    
    if n == 0:
        return 0
    if n == 1:
        return 1
    
    # 迭代法计算，时间复杂度 O(n)，空间复杂度 O(1)
    prev, curr = 0, 1
    for _ in range(2, n + 1):
        prev, curr = curr, prev + curr
    return curr


# 计算 f(10)
result = fibonacci(10)
print(f"斐波那契数列第 10 项的值是: {result}")
