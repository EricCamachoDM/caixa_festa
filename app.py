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

# Função para deletar produto
def deletar_produto(nome):
    st.session_state.produtos = [p for p in st.session_state.produtos if p["nome"] != nome]
    del st.session_state.estoque[nome]

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

# Formulário para deletar produtos
with st.form(key='del_produto'):
    st.subheader("Deletar Produto")
    nome_produto_del = st.selectbox("Selecione o Produto para Deletar", [p["nome"] for p in st.session_state.produtos])
    delete_button = st.form_submit_button(label="Deletar Produto")

    if delete_button:
        deletar_produto(nome_produto_del)
        st.success(f"Produto {nome_produto_del} deletado com sucesso!")

# Exibir produtos disponíveis
st.subheader("Produtos Disponíveis")
produtos_df = pd.DataFrame(st.session_state.produtos)
st.table(produtos_df)

# Formulário para registrar uma venda com múltiplos produtos
with st.form(key='registrar_venda'):
    st.subheader("Registrar Venda")
    produtos_venda = {}
    for produto in st.session_state.produtos:
        quantidade = st.number_input(f"Quantidade de {produto['nome']}", min_value=0, max_value=st.session_state.estoque[produto["nome"]], step=1)
        if quantidade > 0:
            produtos_venda[produto["nome"]] = quantidade

    venda_button = st.form_submit_button(label="Registrar Venda")

    if venda_button:
        valor_total = 0.0
        for produto, quantidade in produtos_venda.items():
            valor_produto = next(p["valor"] for p in st.session_state.produtos if p["nome"] == produto)
            valor_total += quantidade * valor_produto
            st.session_state.estoque[produto] -= quantidade

        st.session_state.caixa += valor_total
        st.session_state.vendas.append({"produtos": produtos_venda, "valor_total": valor_total})
        st.success(f"Venda registrada: {produtos_venda}, Valor Total: R${valor_total:.2f}")

# Exibir vendas realizadas
st.subheader("Vendas Realizadas")
vendas_df = pd.DataFrame(st.session_state.vendas)
st.table(vendas_df)

# Exibir valor em caixa
st.subheader("Caixa")
st.write(f"Valor em Caixa: R${st.session_state.caixa:.2f}")

# Exibir estoque atual
st.subheader("Estoque Atual")
estoque_df = pd.DataFrame.from_dict(st.session_state.estoque, orient='index', columns=['Quantidade'])
st.table(estoque_df)
