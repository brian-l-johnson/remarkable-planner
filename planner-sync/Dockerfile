FROM python:3.12-slim

# poppler-utils provides pdfinfo/pdftoppm required by pdf2image
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py renderer.py recognizer.py ./

EXPOSE 8080

CMD ["python", "app.py"]
