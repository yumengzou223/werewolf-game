FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["python", "-c", "import eventlet; eventlet.monkey_patch(); from main import app, socketio; socketio.run(app, host='0.0.0.0', port=int(__import__('os').environ.get('PORT', 8000)))"]
