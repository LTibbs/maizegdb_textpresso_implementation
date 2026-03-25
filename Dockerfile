FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SKLEARN_ALLOW_DEPRECATED_SKLEARN_PACKAGE_INSTALL=True \
    NLTK_DATA=/usr/local/share/nltk_data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt setup.py /app/
COPY textpresso_classifiers /app/textpresso_classifiers
COPY bin /app/bin
COPY tests /app/tests
COPY wormbase_tools /app/wormbase_tools
COPY sorghumbase_textpresso_implementation /app/sorghumbase_textpresso_implementation

RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt \
    && pip install -e . \
    && python -m nltk.downloader punkt punkt_tab wordnet omw-1.4

CMD ["python", "-m", "unittest", "discover", "-s", "tests"]
