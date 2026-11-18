FROM python:3.11-slim
WORKDIR /app
COPY . .
CMD ["python","-m","robot_shift.cli","data/example.json"]
