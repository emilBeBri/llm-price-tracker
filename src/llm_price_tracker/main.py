import typer

app = typer.Typer()

@app.command()
def dev():
    """Run in development mode."""
    pass

@app.command()
def start():
    """Run in production mode."""
    pass

if __name__ == "__main__":
    app()
