          pre-commit run --all-files || true
          pre-commit run --hook-stage manual python-typing-update --check --py314-plus --ruff || true
          python -m ruff check custom_components/ \         
            --preview \
            --fix-all \ 
            --add-noqa \
            --unsafe-fixes \
            --extend-select RUF100 \
            --py314-plus \
            --output-format=github \
            --exit-zero || true
