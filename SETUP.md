### Manual Publishing

```bash
# Navigate to SDK folder
cd api-monitor-sdk

# Install build tools
pip install build twine

# Build package
python -m build

# Upload to TestPyPI (test first)
twine upload --repository testpypi dist/*

# Test installation
pip install --index-url https://test.pypi.org/simple/ api-monitor-sdk

# Upload to real PyPI
twine upload dist/*

# Test real installation
pip install api-monitor-sdk
```

### Automated Publishing (GitHub Actions)

**.github/workflows/publish.yml:**
```yaml
name: Publish to PyPI

on:
  release:
    types: [created]

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install build twine
    
    - name: Build package
      run: python -m build
    
    - name: Publish to PyPI
      env:
        TWINE_USERNAME: __token__
        TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
      run: twine upload dist/*
```

**Setup:**
1. Go to GitHub repo → Settings → Secrets → Actions
2. Add secret: `PYPI_API_TOKEN` with your PyPI token
3. Create a release on GitHub
4. Package automatically published to PyPI

### Versioning

Use semantic versioning (SemVer):
- `0.1.0` - Initial release
- `0.2.0` - New features (backward compatible)
- `0.2.1` - Bug fixes
- `1.0.0` - Production ready
- `2.0.0` - Breaking changes

Update version in:
- `setup.py`
- `apimonitor/__init__.py`
- Tag GitHub release with same version