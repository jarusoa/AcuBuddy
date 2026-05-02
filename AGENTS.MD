# AcuBuddy — AI-Powered Acumatica Coding Assistant

## Project Overview
AcuBuddy is a RAG-powered AI assistant for Acumatica xRP platform development,
running inside VS Code via OpenCode + DeepSeek V4, using a local Flask-based
RAG server that retrieves context from Acumatica documentation, coding standards,
and code examples before answering.

## Tech Stack
- Python 3.11+ (Flask, ChromaDB, sentence-transformers) for the RAG server
- DeepSeek V4-Pro API for LLM inference (OpenAI-compatible endpoint)
- OpenCode (terminal-based AI coding agent) for IDE integration
- VS Code as the primary development environment

## Code Conventions
- Follow PEP 8 for Python
- Use type hints everywhere
- Environment variables via `.env` (never commit real secrets)
- All knowledge documents live in `knowledge/` folder
- Vector database persists in `vector_db/` folder

## Rules for This Project
1. Answer questions using ONLY the provided context from the RAG pipeline.
   If context is insufficient, say so clearly — never guess.
2. When writing Acumatica C# code, follow the naming conventions and patterns
   found in the knowledge base (e.g., use `Usr` prefix for custom fields).
3. Structure development answers as: direct answer → code example → source citations.
4. Keep answers concise and actionable — assume the user is an experienced
   .NET developer.
5. For code generation, always add comments explaining key logic.