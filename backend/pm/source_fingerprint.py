"""A decision applies to the cited version, never silently to a different source."""
import hashlib
import json


def proposal_fingerprint(target,operation,payload,source_refs):
    value={'target':target,'operation':operation,'payload':payload}
    if source_refs:
        value['source_refs']=sorted(set(source_refs))
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
