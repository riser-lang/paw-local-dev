"""Extract ASCII and UTF-16LE strings from Inno Setup CompiledCode.bin."""
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def extract_strings(bin_file: Path) -> dict:
    data = bin_file.read_bytes()
    # Printable ASCII
    ascii_matches = re.findall(rb'[\x20-\x7e]{4,}', data)
    ascii_strs = [m.decode("ascii") for m in ascii_matches]
    
    # UTF-16LE (Latin-1 / printable range encoded in UTF-16LE)
    utf16_matches = re.findall(rb'(?:[\x20-\x7e]\x00){4,}', data)
    utf16_strs = [m.decode("utf-16le") for m in utf16_matches]
    
    return {
        "file": str(bin_file),
        "size": len(data),
        "ascii_strings": ascii_strs,
        "utf16_strings": utf16_strs,
    }

def main():
    target = None
    candidates = [
        ROOT_DIR / "03_Decompilation_And_Binary_Fidelity" / "CompiledCode.bin",
        Path(r"C:\Users\sunao\.gemini\antigravity\brain\af0f00d3-464e-40fc-bd2f-fd235c3a6395\scratch\test_innounp\embedded\CompiledCode.bin"),
        Path(r"C:\Users\sunao\Desktop\PAW_Claude_Judge_Package_v2.3.1\03_Decompilation_And_Binary_Fidelity\CompiledCode.bin"),
    ]
    for c in candidates:
        if c.is_file():
            target = c
            break
            
    if not target:
        print("[ERROR] CompiledCode.bin not found")
        sys.exit(1)
        
    res = extract_strings(target)
    out_file = ROOT_DIR / "verification_logs" / "bytecode_strings.log"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"=== CompiledCode.bin String Extraction Log ===\n")
        f.write(f"Source file: {res['file']}\n")
        f.write(f"File size: {res['size']} bytes\n\n")
        f.write(f"--- ASCII Strings ({len(res['ascii_strings'])} found) ---\n")
        for s in res['ascii_strings']:
            f.write(f"  {s}\n")
        f.write(f"\n--- UTF-16LE Strings ({len(res['utf16_strings'])} found) ---\n")
        for s in res['utf16_strings']:
            f.write(f"  {s}\n")
            
    print(f"Extracted {len(res['ascii_strings'])} ASCII and {len(res['utf16_strings'])} UTF-16LE strings to {out_file}")

if __name__ == "__main__":
    main()
