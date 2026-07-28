from mem0 import Memory

memory_config = {
    "llm": {"provider": "ollama", "config": {"model": "llama3.1:8b", "temperature": 0.0, "ollama_base_url": "http://localhost:11434"}},
    "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text", "ollama_base_url": "http://localhost:11434"}},
    "vector_store": {"provider": "qdrant", "config": {"host": "localhost", "port": 6333, "collection_name": "ariel_memories_v2", "embedding_model_dims": 768}},
}

print("Initializing Memory...")
mem = Memory.from_config(memory_config)
print("Initialized. Trying get_all...")
result = mem.get_all(user_id="ariel_test_01")
print("Got result:", result)