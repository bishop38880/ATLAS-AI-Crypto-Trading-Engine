import asyncio
import pybreaker

breaker = pybreaker.CircuitBreaker(fail_max=1)

@breaker
async def fail():
    raise Exception("fail")

async def main():
    try:
        await fail()
    except Exception:
        pass
    print(breaker.state.name)

asyncio.run(main())
