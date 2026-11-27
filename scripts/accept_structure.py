"""Accept source-reviewed Document Structuring output."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from knowledge_foundry.knowledge_engineering.structuring.document_structuring import main
if __name__ == '__main__':
    raise SystemExit(main())
