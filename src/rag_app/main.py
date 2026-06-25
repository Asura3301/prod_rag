from dotenv import load_dotenv
from importlib.metadata import version
import os

load_dotenv()

core_version = version("langchain-core")
lg_graph_version = version("langgraph")
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

print(f"langchain-core version: {core_version}")
print(f"langgraph version: {lg_graph_version}")

#----------------------------------------------------------
# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_BASE = os.getenv("API_BASE")
MODEL_ID = os.getenv("MODEL_ID")

#----------------------------------------------------------
def main():
    llm = ChatOpenAI(
        model=MODEL_ID,
        base_url=API_BASE, 
        api_key=OPENAI_API_KEY,
        temperature=0.0,
        max_tokens=2048,
    )
    response = llm.invoke("Say 'Setup Complete' in one sentence.")    
    print(f"Response: {response.content}")
    
    print("Setup Complete!")

if __name__ == "__main__":
    main()
