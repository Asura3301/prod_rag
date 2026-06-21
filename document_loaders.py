import os
import tempfile
from pathlib import Path
from langchain_community.document_loaders import (TextLoader) # Depricated version (MUST BE REPLACED)

from langchain_core.documents import Document
from langchain_docling.loaders import DoclingLoader, ExportType
from docling.chunking import HybridChunker

from dotenv import load_dotenv

load_dotenv()

#----------------------------------------------------------
# Configuration
FILE_PATH = os.getenv("FILE_PATH")
EXPORT_TYPE = ExportType.DOC_CHUNKS
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID")

#----------------------------------------------------------
# Test Text Loader
def load_text_file():
    # Create temporary text file for demonstration
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_file:
        temp_file.write(b"This is a test file.\nThis file is used to test the text loader.")
        temp_file_path = temp_file.name
        
    try:
        # Load the text file using TextLoader
        loader = TextLoader(temp_file_path)
        documents = loader.load()
        
        print(f"Loaded {len(documents)} document(s)")
        print(f"Content preview: {documents[0].page_content[:100]}...")
        print(f"Metadata: {documents[0].metadata}")
        
        # Print the loaded documents
        # for doc in documents:
        #     print("Document Content:")
        #     print(doc)
        #     print(doc.page_content)
    
    finally:
        # Delete the temporary file
        os.remove(temp_file_path)
    
    
#----------------------------------------------------------
# Docling Loader
class DoclingLoader:
    def load(self, file_path: str | Path):
        path = Path(file_path)
        
        loader = DoclingLoader(
            file_path=str(path),
            export_type=EXPORT_TYPE,
            chunker=HybridChunker(tokenizer=EMBED_MODEL_ID)
        )
        docs = loader.load()

        print(f"Loaded {len(docs)} document(s) from PDF")
        for doc in docs:
            doc.metadata.update(
                {
                    "source": str(path),
                    "filename": path.name,
                    "suffix": path.suffix.lower(),
                    "loader": "docling",
                }
            )
            print(f"{doc.page_content[:100]}")
        return docs
    
#---------
# Unstructured Loader
class UnstructuredLoader:
    #TODO


#----------------------------------------------------------
if __name__ == "__main__":
    load_text_file()