
# **Problem Statement 1: Verifiable Source Attribution with Granular PDF Indexing**

## **The Problem**
Current RAG implementations for PDF documents suffer from a critical transparency gap: when systems retrieve information from PDFs, they cannot provide **granular, verifiable source citations** (page numbers, line numbers, or exact text coordinates). Users receive answers without the ability to audit where specific claims originated within the source document. This creates trust issues in high-stakes domains (legal, medical, academic) where source verification is mandatory.

**Existing solutions either:**
- Return entire pages as sources (too broad)
- Lose structural metadata during text extraction
- Fail to track exact text positions through the embedding/retrieval pipeline
- Cannot verify if the LLM answer actually matches the cited source text

## **Proposed Solution Architecture**

### **Indexing Strategy with Structural Preservation:**
```json
{
  "chunk_id": "doc_001_p5_l23-45",
  "content": "extracted text...",
  "metadata": {
    "source_pdf": "contract_v2.pdf",
    "page_number": 5,
    "line_start": 23,
    "line_end": 45,
    "bbox": [120.5, 300.2, 450.8, 320.1],
    "section_header": "Clause 3.2",
    "surrounding_context": "..."
  }
}
```

### **Tech Stack Implementation:**
| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Framework** | CrewAI + LangChain | Multi-agent orchestration and document processing |
| **Parsing** | PyPDFLoader + pdfplumber | Spatial text extraction with coordinates |
| **Vector DB** | Weaviate / Pinecone | Metadata-enriched storage with filtering |
| **Evaluation** | RAGAS | Citation accuracy and faithfulness metrics |
| **Cache** | Redis | Frequent query optimization |
| **Analytics** | Snowflake | Citation pattern analysis |
| **Audit** | DynamoDB | Query/answer logging with source coordinates |

### **Verification Workflow:**
1. **Ingestion:** Parse PDFs preserving layout structure using `pdfplumber` or `pymupdf` to extract text with coordinates
2. **Indexing:** Chunk with overlapping windows, storing page/line metadata in vector DB
3. **Retrieval:** Hybrid search (semantic + metadata filters for specific pages)
4. **Generation:** Constrained generation requiring citations in format `[Source: Page X, Lines Y-Z]`
5. **Validation:** RAGAS evaluates **Faithfulness** (claims supported by context) and **Citation Precision** (correct source attribution)





---







# **Problem Statement 2: Hallucination-Resistant PDF Question Answering with Source-Grounded Constraints**

## **The Problem**
Standard RAG systems often generate answers that **hallucinate information not present in retrieved PDF chunks** or **incorrectly synthesize across multiple documents**. Without strict source-grounding constraints, LLMs:
- Add external knowledge not in the PDF corpus
- Misattribute information to wrong documents/pages
- Generate plausible-sounding but unverified claims
- Fail to indicate when information is missing from the PDF

Current evaluation methods lack automated verification that answers are strictly derived from indexed PDF content with explicit traceability.

## **Proposed Solution Architecture**

### **Constraint-Based Generation Framework:**

**System Prompt (Strict Source Constraint):**
```
You are a PDF research assistant. You may ONLY use information 
from the provided PDF chunks. For every claim, you MUST cite:
- Document name
- Page number  
- Line number range
- Exact quoted text in brackets

If the answer is not in the provided chunks, respond: 
"The provided PDFs do not contain information about [query topic]."
```

### **CrewAI Agent Architecture:**
- **Retrieval Agent:** Uses RAGTool with Weaviate/Pinecone metadata filtering
- **Verification Agent:** Cross-checks generated claims against retrieved chunks using semantic similarity
- **Citation Formatter:** Structures output with verified page/line references
- **Safety Agent:** Detects hallucinations using RAGAS **Faithfulness** metric

### **Technical Implementation:**
```python
class VerifiedPDFTool(BaseTool):
    name: str = "verified_pdf_search"
    
    def _run(self, query: str):
        chunks = vector_db.search(
            query, 
            filter={"source_type": "pdf"},
            include_metadata=True
        )
        
        verified_chunks = [
            c for c in chunks 
            if c.metadata.get('page_number') 
            and c.metadata.get('line_start')
        ]
        
        return {
            "context": verified_chunks,
            "source_metadata": [c.metadata for c in verified_chunks]
        }
```

### **Evaluation with RAGAS:**
| Metric | Purpose |
|--------|---------|
| **Context Precision** | Measures if retrieved chunks are relevant |
| **Faithfulness** | Verifies claims are supported by context |
| **Answer Relevancy** | Ensures response addresses query without drifting |
| **Context Recall** | Checks if all necessary PDF information was retrieved |

---

## **Comparative Analysis: Key Differentiators**

| Feature | Standard RAG | Proposed Solution |
|---------|-------------|-------------------|
| **Source Granularity** | Document-level | Page + Line number precision |
| **Verification** | Manual checking | Automated RAGAS citation accuracy |
| **Hallucination Prevention** | Basic prompting | Multi-agent constraint checking |
| **Audit Trail** | None | DynamoDB logged with coordinates |
| **Evaluation** | Generic metrics | Domain-specific RAGAS + custom metrics |

---

