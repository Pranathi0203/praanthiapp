from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {
        "message": "Pranathi app is running",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/hello/{name}")
def hello(name: str):
    return {"message": f"Hello, {name}!"}
