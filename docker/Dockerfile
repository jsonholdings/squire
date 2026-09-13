# squire's Docker image: stdlib-only python3, no extra deps.
FROM python:3-slim

WORKDIR /app
COPY squire.py /app/squire.py
RUN chmod +x /app/squire.py

ENV SQUIRE_OLLAMA=http://ollama:11434
# squire refuses non-localhost backends unless allow-listed; "ollama" is the
# compose service hostname this image talks to.
ENV SQUIRE_ALLOW_HOSTS=ollama
ENV SQUIRE_LEDGER=/data/ledger.jsonl

ENTRYPOINT ["python3", "/app/squire.py"]
CMD ["--help"]
