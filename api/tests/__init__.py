"""Suíte da API.

É um pacote (e não apenas um diretório) porque os testes importam helpers de
`tests.conftest`; sem `__init__.py`, o mypy vê o mesmo arquivo com dois nomes de módulo.
"""
