import asyncio
import os
import re
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()

URL, KEY = str(os.environ.get("AI_SERVER_URL")), str(os.environ.get("AI_SERVER_KEY"))

if not URL or not KEY:
    raise ValueError(
        "AI_SERVER_URL and AI_SERVER_KEY must be set in the environment variables."
    )

PROMPT = """You are a HTTP server named "AI HTTP Server" powered completely by artificial intelligence.

REQUEST HANDLING:
- You will receive raw HTTP requests (headers and body) from clients
- You will parse the request and understand what the client is asking for based on the requested URL, parameters, cookies, etc.
- You may utilize the referrer header to determine the context of the request if desired
- You will not use any external libraries or frameworks to handle/generate the request

RESPONSE GENERATION:
- You will respond with what you think is the best fit response to the request (webpage or file)
- Your response MUST be formatted as a valid HTTP response (do NOT use a "Content-length" header)
- If a file is requested, respond only with the file content and appropriate response based on its extension
- Do not respond with raw images, videos, or other media files

WEBPAGE GUIDELINES:
- If responding with a webpage, use only HTML, CSS, and JavaScript
- ALWAYS make pages look good, functional, and responsive with a modern theme
- ALWAYS include a title in the HTML
- You may include an external favicon link in the HTML
- NEVER include placeholders, always use real or mock data
- NEVER explain anything to the user or apologize

LINKS AND NAVIGATION:
- The index page (root URL "/") should be a well-designed homepage with links to other pages
- For the index page, include at least 5 links to other interesting pages on the server
- Always include at least 3 links to pages that are not the index page
- Use descriptive names for both the links and their paths (not "link1", "link2", etc.)
- Add multiple links to various content on the website (pages, files, resources)
- NEVER include links like "https://example.com" - use correct links based on request context
- NEVER include external resource links unless specifically requested

TECHNICAL REQUIREMENTS:
- NEVER include comments in your output
- NEVER include links to CSS or JavaScript files, always include them inline or use external links
- NEVER utilize placeholders or TODOs - complete all code fully
"""

IP, PORT = "0.0.0.0", 8000
RATELIMITE_TABLE = []
SITEMAP = []
GENERATED_FILES_PATH = Path("generated")


async def get_response(prompt: str) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "model": "deepseek-chat",
            }
            async with session.post(
                URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {KEY}",
                },
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
    except aiohttp.ClientError as e:
        print(f"Error fetching response: {e}")
        return {"error": str(e)}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")[0]
    print(f"Connection from {addr}")

    if addr in RATELIMITE_TABLE:
        print(f"Rate limit exceeded for {addr}. Closing connection.")
        response = "HTTP/1.1 429 Too Many Requests\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nRatelimit exceeded. Please try again later."
        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    RATELIMITE_TABLE.append(addr)

    buffer = ""
    while "\r\n\r\n" not in buffer:
        chunk = await reader.read(8192)
        if not chunk:
            writer.close()
            await writer.wait_closed()
            return
        buffer += chunk.decode("utf-8", errors="ignore")

    first_line = buffer.split("\r\n")[0]
    http_pattern = r"^(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH) .+ HTTP/\d\.\d$"
    if not re.match(http_pattern, first_line):
        print(f"Non-HTTP request from {addr}: {first_line}")
        response = "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nInvalid HTTP request"
        writer.write(response.encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    response = None
    true_path = first_line.split(" ")[1]
    path = true_path.lstrip("/").replace("/", "|")
    if true_path == "/" or path not in SITEMAP:
        SITEMAP.append(path)
        print(f"New endpoint added to sitemap: {path}")

        response = (
            (await get_response(buffer))
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        if true_path != "/":
            file_path = GENERATED_FILES_PATH / Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(response)
            except Exception as e:
                print(f"Error saving generated response: {e}")
                response = "HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nInvalid request."
            print(f"Generated response saved to {file_path}")
    else:
        if (GENERATED_FILES_PATH / Path(path)).exists():
            file_path = GENERATED_FILES_PATH / Path(path)
            if file_path.is_file():
                with open(file_path, "r", encoding="utf-8") as f:
                    response = f.read()
            else:
                response = "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nThis isn't supposed to happen D:"
        else:
            print(f"Sitemap path not found: {path}")
            response = "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nThis isn't supposed to happen D:"

    writer.write(response.encode("utf-8"))
    await writer.drain()

    writer.close()
    await writer.wait_closed()


async def run_server():
    server = await asyncio.start_server(handle_client, IP, PORT)
    addrs = ", ".join(
        [f"{sock.getsockname()[0]}:{sock.getsockname()[1]}" for sock in server.sockets]
    )
    print(f"Serving on {addrs}")
    async with server:
        await server.serve_forever()


async def clear_rate_limit_table():
    while True:
        await asyncio.sleep(3)
        RATELIMITE_TABLE.clear()


async def clear_generated_endpoints():
    global SITEMAP
    while True:
        await asyncio.sleep(5 * 60)
        SITEMAP.clear()
        for file in GENERATED_FILES_PATH.glob("**/*"):
            if file.is_file():
                file.unlink()
        print("Sitemap cleared.")


async def main():
    if not GENERATED_FILES_PATH.exists():
        GENERATED_FILES_PATH.mkdir(parents=True, exist_ok=True)

    for file in GENERATED_FILES_PATH.glob("**/*"):
        if file.is_file():
            file.unlink()

    asyncio.create_task(clear_rate_limit_table())
    asyncio.create_task(clear_generated_endpoints())
    print("Starting AI HTTP Server...")
    await run_server()


if __name__ == "__main__":
    asyncio.run(main())
