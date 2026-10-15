"""A source claim is complete only after its owner releases it."""
def wait_until_idle(condition, active, ident, timeout=60):
    with condition:
        if not condition.wait_for(lambda: ident not in active, timeout):
            raise TimeoutError('资料分析尚未完成，请稍后重试')
