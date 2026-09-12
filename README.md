# Atlas da Arte Pré-Histórica — Python

Aplicação web feita em Python com Streamlit e Folium.

## Estrutura

```text
app.py
image_resolver.py
requirements.txt
.streamlit/config.toml
data/locais.json
```

Depois que as imagens forem resolvidas, também existirá:

```text
data/locais_resolvidos.json
```

O `app.py` detecta esse arquivo automaticamente e passa a utilizá-lo.

## Executar localmente

Requer Python 3.11 ou superior.

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Resolver as imagens do Wikimedia Commons

Modo automático:

```bash
python image_resolver.py --input data/locais.json --output data/locais_resolvidos.json --contact "https://github.com/SEU_USUARIO/SEU_REPOSITORIO"
```

Modo recomendado, com escolha manual das imagens:

```bash
python image_resolver.py --interactive --input data/locais.json --output data/locais_resolvidos.json --contact "https://github.com/SEU_USUARIO/SEU_REPOSITORIO"
```

Depois execute novamente:

```bash
streamlit run app.py
```

## Publicar no Streamlit Community Cloud

1. Suba todo o conteúdo deste projeto para um repositório no GitHub.
2. Entre em `share.streamlit.io` usando sua conta GitHub.
3. Crie um novo app.
4. Selecione o repositório e a branch `main`.
5. Em arquivo principal/entrypoint, escolha `app.py`.
6. Faça o deploy.

O Streamlit Cloud lê `requirements.txt`, instala as dependências e executa o Python no servidor.
