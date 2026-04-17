# OpenClaw E2E Test Fixture

This document is used by the OpenClaw E2E test suite to verify that the
assistant can open and read local files from the filesystem.

## Project Overview

OpenClaw is a personal AI assistant CLI that runs as a Docker container on
a Mac Mini home server. It supports multiple AI providers and routes queries
to the most appropriate model based on context and content type.

## Key Facts

- **Project name**: OpenClaw
- **Primary host**: Mac Mini at 192.168.1.93
- **Primary language**: Python
- **Deployment**: Docker container with `make ship`
- **Supported providers**: Copilot, Perplexity, Gemini, Tavily

## Features

- Intelligent model routing based on query type
- Local file injection when file paths are mentioned in prompts
- Slash commands: /save, /retry, /tldr, /why, /inject
- E2E test harness with repeatable query validation

## Summary

OpenClaw combines multiple AI providers into a single personal assistant
accessible from any device on the home network. The routing system selects
the best model for each query automatically.
