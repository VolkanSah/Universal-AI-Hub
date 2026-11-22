# Use an official Python runtime as a parent image.
# We choose a slim version to keep the image size small.
FROM python:3.10-slim

# Set the working directory in the container.
# All subsequent commands will be executed in this directory.
WORKDIR /app

# Copy the requirements file into the container.
# We do this first to leverage Docker's layer caching.
# If requirements.txt doesn't change, this step is skipped.
COPY requirements.txt ./

# Install any needed packages specified in requirements.txt.
# The --no-cache-dir flag helps to keep the image smaller.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the working directory.
COPY . .

# Expose a port if your application is a web server.
# For example, if your application runs on port 8000.
# EXPOSE 8000

# Define the command to run your application.
# This command will be executed when the container starts.
# We use main.py as the entry point, as per our architecture.
CMD ["python", "main.py"]
