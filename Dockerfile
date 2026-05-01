FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads

EXPOSE 5000

ENV FLASK_ENV=production
ENV MOCK_MODE=1

CMD ["python", "app.py"]
