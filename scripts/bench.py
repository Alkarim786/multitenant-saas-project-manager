"""Measures card moves and live delivery against the running stack. Prints real numbers only.

    SEED_PASSWORD=... python scripts/bench.py --url http://localhost:3000

1. Move latency: POST /issues/{id}/move/ at 1 and 16 concurrent clients, each on its own cards.
2. Same-card contention: 16 clients race on one card with its version, and the 409s are counted.
3. Delivery latency: from sending a move to an observer's WebSocket receiving it.
"""

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid

import httpx
import websockets


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))]


def summary(values):
    return f"p50 {pct(values, 50):.1f} ms, p95 {pct(values, 95):.1f} ms, p99 {pct(values, 99):.1f} ms, mean {statistics.mean(values):.1f} ms (n={len(values)})"


async def moves(client, cards, columns, count, out):
    for n in range(count):
        card = cards[n % len(cards)]
        t = time.perf_counter()
        r = await client.post(f"/api/issues/{card}/move/", json={"column": columns[n % 2]})
        out.append((time.perf_counter() - t) * 1000)
        r.raise_for_status()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:3000")
    ap.add_argument("--moves", type=int, default=300)
    args = ap.parse_args()
    password = os.environ["SEED_PASSWORD"]

    async with httpx.AsyncClient(base_url=args.url, timeout=60) as anon:
        r = await anon.post("/api/auth/token/", json={"email": "owner@acme.test", "password": password})
        r.raise_for_status()
        token = r.json()["access"]
        me = await anon.get("/api/me/", headers={"Authorization": f"Bearer {token}"})
        tenant = next(o["id"] for o in me.json()["organisations"] if o["slug"] == "acme")

    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant)}
    limits = httpx.Limits(max_connections=64)
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60, limits=limits) as c:
        key = "B" + uuid.uuid4().hex[:8].upper()
        project = (await c.post("/api/projects/", json={"key": key, "name": "Benchmark"})).raise_for_status().json()["id"]
        board = (await c.get(f"/api/projects/{project}/board/")).json()
        columns = [col["id"] for col in board["columns"]][:2]

        async def create(title):
            r = await c.post("/api/issues/", json={"project": project, "column": columns[0], "title": title})
            return r.raise_for_status().json()["id"]

        cards = [await create(f"card {n}") for n in range(160)]

        for clients in (1, 16):
            latencies = []
            per_client = args.moves // clients
            start = time.perf_counter()
            await asyncio.gather(*(moves(c, cards[i * 10 : i * 10 + 10], columns, per_client, latencies) for i in range(clients)))
            elapsed = time.perf_counter() - start
            print(f"move, {clients:>2} client(s): {summary(latencies)}, {len(latencies) / elapsed:.0f} moves/s")

        # Contention: everyone holds the same version, so exactly one may win each round.
        target = cards[0]
        wins = conflicts = 0
        for _ in range(20):
            current = (await c.get(f"/api/issues/{target}/")).json()
            answers = await asyncio.gather(
                *(c.post(f"/api/issues/{target}/move/", json={"column": columns[1], "version": current["version"]}) for _ in range(16))
            )
            codes = [a.status_code for a in answers]
            wins += codes.count(200)
            conflicts += codes.count(409)
            assert set(codes) <= {200, 409}, codes
        print(f"contention, 20 rounds of 16 clients on one card: {wins} succeeded, {conflicts} got 409")

        # Delivery: one writer, one observer connected through the same public entry point.
        ws_url = args.url.replace("http", "ws", 1) + f"/ws/boards/{project}/?token={token}&tenant={tenant}"
        delivery = []
        async with websockets.connect(ws_url) as ws:
            for n in range(args.moves // 3):
                card = cards[n % len(cards)]
                t = time.perf_counter()
                (await c.post(f"/api/issues/{card}/move/", json={"column": columns[n % 2]})).raise_for_status()
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if message["event"] == "issue.moved" and message["payload"]["id"] == card:
                        delivery.append((time.perf_counter() - t) * 1000)
                        break
        print(f"move to WebSocket delivery: {summary(delivery)}")

        await c.delete(f"/api/projects/{project}/")


if __name__ == "__main__":
    asyncio.run(main())
