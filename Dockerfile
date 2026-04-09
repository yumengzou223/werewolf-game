FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["gunicorn", "--worker-class", "socketio.sgunicorn.GeventSocketIOWorker", "--workers", "1", "--bind", "0.0.0.0:8000", "main:app"]
