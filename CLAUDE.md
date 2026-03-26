# Claude Instructions

## At the Start of Every Session

Read [tasks/LESSONS.md](tasks/LESSONS.md) at the beginning of every conversation, and again before writing or modifying any code.
This file contains past mistakes and corrections — apply the rules there to avoid repeating them.

## NER Pipeline Rule

Always use `en_core_web_trf` (the spaCy transformer model) for NER tagging.
Never fall back to `en_core_web_sm` or any smaller model — sm-tagged chunks produce significantly lower-quality entity extraction.
If `en_core_web_trf` is not installed, run: `python -m spacy download en_core_web_trf`

## Project Overview

This is the `sw-llm-knowledge-graph` project. Refer to [specifications/](specifications/) and [requirements/](requirements/) for context on what is being built.
