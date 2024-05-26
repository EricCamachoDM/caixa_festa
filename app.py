import streamlit as st
import pandas as pd

# Configuração inicial
st.title("Controle de Estoque e Caixa para Festa Beneficente")

# Sessões de estado para manter o estoque e as vendas
if "produtos" not in st.session_state:
    st.session_state.produtos = []
if "estoque" not in st.session_state:
    st.session_state.estoque = {}
if "vendas" not in st.session_state:
    st.session_state.vendas = []
if "caixa" not in st.session_state:
    st.session_state.caixa = 0.0

# Função para adicionar produto
def adicionar_produto(nome, valor, quantidade):
    st.session_state.produtos.append({"nome": nome, "valor": valor, "quantidade": quantidade})
    st.session_state.estoque[nome] = quantidade

# Formulário para adicionar novos produtos
with st.form(key='add_produto'):
    st.subheader("Adicionar Produto")
    nome_produto = st.text_input("Nome do Produto")
    valor_unitario = st.number_input("Valor Unitário", min_value=0.0, format="%.2f")
    quantidade_estoque = st.number_input("Quantidade em Estoque", min_value=0, step=1)
    submit_button = st.form_submit_button(label="Adicionar Produto")

    if submit_button:
        adicionar_produto(nome_produto, valor_unitario, quantidade_estoque)
        st.success(f"Produto {nome_produto} adicionado com sucesso!")

# Exibir produtos disponíveis
st.subheader("Produtos Disponíveis")
produtos_df = pd.DataFrame(st.session_state.produtos)
st.table(produtos_df)

# Formulário para registrar uma venda
with st.form(key='registrar_venda'):
    st.subheader("Registrar Venda")
    produto_venda = st.selectbox("Selecione o Produto", [p["nome"] for p in st.session_state.produtos])
    quantidade_venda = st.number_input("Quantidade", min_value=1, step=1)
    venda_button = st.form_submit_button(label="Registrar Venda")

    if venda_button:
        if st.session_state.estoque[produto_venda] >= quantidade_venda:
            valor_total = quantidade_venda * next(p["valor"] for p in st.session_state.produtos if p["nome"] == produto_venda)
            st.session_state.caixa += valor_total
            st.session_state.estoque[produto_venda] -= quantidade_venda
            st.session_state.vendas.append({"produto": produto_venda, "quantidade": quantidade_venda, "valor_total": valor_total})
            st.success(f"Venda registrada: {produto_venda}, Quantidade: {quantidade_venda}, Valor Total: R${valor_total:.2f}")
        else:
            st.error("Estoque insuficiente!")

# Exibir vendas realizadas
st.subheader("Vendas Realizadas")
vendas_df = pd.DataFrame(st.session_state.vendas)
st.table(vendas_df)

# Exibir valor em caixa
st.subheader("Caixa")
st.write(f"Valor em Caixa: R${st.session_state.caixa:.2f}")
