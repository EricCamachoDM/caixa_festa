import streamlit as st
import pandas as pd
import requests # Para buscar dados do GitHub
from io import StringIO # Para ler o CSV da string baixada
from datetime import datetime # Para registrar o horário da venda

# --- Configurações e Constantes ---
APP_TITLE = "Controle de Estoque e Caixa para Festa Macarronada"

GITHUB_CSV_URL = "https://raw.githubusercontent.com/EricCamachoDM/caixa_festa/refs/heads/main/produtos_estoque.csv" 

# --- Funções Auxiliares ---

@st.cache_data(ttl=300) # Cache por 5 minutos para não buscar toda hora no GitHub
def carregar_produtos_do_github(url: str) -> pd.DataFrame | None:
    """
    Carrega os dados dos produtos de um arquivo CSV no GitHub.
    Retorna um DataFrame do Pandas ou None em caso de erro.
    """
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        csv_data = StringIO(response.text)
        df = pd.read_csv(csv_data)
        if not all(col in df.columns for col in ["nome", "valor", "quantidade"]):
            st.error("O arquivo CSV não contém as colunas esperadas: 'nome', 'valor', 'quantidade'.")
            return None
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
        df['quantidade'] = pd.to_numeric(df['quantidade'], errors='coerce')
        df.dropna(subset=['nome', 'valor', 'quantidade'], inplace=True)
        return df
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao buscar dados do GitHub: {e}")
        return None
    except pd.errors.EmptyDataError:
        st.error("O arquivo CSV está vazio ou mal formatado.")
        return None
    except Exception as e:
        st.error(f"Erro ao processar o arquivo CSV: {e}")
        return None

def inicializar_estado_sessao():
    """Inicializa o estado da sessão com os dados dos produtos e outras variáveis."""
    if "produtos_carregados" not in st.session_state:
        st.session_state.produtos_carregados = False

    if not st.session_state.produtos_carregados:
        df_produtos = carregar_produtos_do_github(GITHUB_CSV_URL)
        if df_produtos is not None and not df_produtos.empty:
            st.session_state.produtos = df_produtos.to_dict('records')
            st.session_state.estoque = {
                produto["nome"]: int(produto["quantidade"]) for produto in st.session_state.produtos
            }
            st.session_state.produtos_carregados = True
        else:
            st.session_state.produtos = []
            st.session_state.estoque = {}
            st.warning("Não foi possível carregar os produtos do GitHub ou o arquivo está vazio. Verifique a URL e o conteúdo do CSV.")

    if "vendas" not in st.session_state:
        st.session_state.vendas = []
    if "caixa" not in st.session_state:
        st.session_state.caixa = 0.0

# --- Funções de Negócio ---

def adicionar_produto_sessao(nome: str, valor: float, quantidade: int):
    """Adiciona um produto ao estado da sessão (não persiste no GitHub)."""
    if any(p["nome"] == nome for p in st.session_state.produtos):
        st.warning(f"Produto '{nome}' já existe. Para atualizar, delete e adicione novamente.")
        return
    st.session_state.produtos.append({"nome": nome, "valor": valor, "quantidade": quantidade})
    st.session_state.estoque[nome] = quantidade
    st.success(f"Produto '{nome}' adicionado à sessão atual.")
    st.info("Nota: Produtos adicionados aqui não são salvos permanentemente no arquivo do GitHub.")

def deletar_produto_sessao(nome: str):
    """Deleta um produto do estado da sessão (não persiste no GitHub)."""
    st.session_state.produtos = [p for p in st.session_state.produtos if p["nome"] != nome]
    if nome in st.session_state.estoque:
        del st.session_state.estoque[nome]
    st.success(f"Produto '{nome}' deletado da sessão atual.")
    st.info("Nota: Produtos deletados aqui não afetam o arquivo permanente no GitHub.")

def registrar_venda(produtos_venda: dict):
    """Registra uma venda, atualiza o estoque, o caixa e adiciona timestamp."""
    if not produtos_venda:
        st.warning("Nenhum produto selecionado para a venda.")
        return None, 0.0

    valor_total = 0.0
    itens_venda_detalhado = {}
    horario_venda = datetime.now() # Captura o horário da venda

    for nome_produto, quantidade_vendida in produtos_venda.items():
        if quantidade_vendida <= 0:
            continue

        produto_info = next((p for p in st.session_state.produtos if p["nome"] == nome_produto), None)
        if not produto_info:
            st.error(f"Produto '{nome_produto}' não encontrado na lista de produtos.")
            continue

        if st.session_state.estoque.get(nome_produto, 0) < quantidade_vendida:
            st.error(f"Estoque insuficiente para '{nome_produto}'. Disponível: {st.session_state.estoque.get(nome_produto, 0)}")
            return None, 0.0

        valor_produto = produto_info["valor"]
        valor_total += quantidade_vendida * valor_produto
        st.session_state.estoque[nome_produto] -= quantidade_vendida
        itens_venda_detalhado[nome_produto] = quantidade_vendida

    if not itens_venda_detalhado:
        st.warning("Nenhum item válido na venda.")
        return None, 0.0

    venda_id = len(st.session_state.vendas) + 1
    st.session_state.caixa += valor_total
    st.session_state.vendas.append({
        "id": venda_id,
        "produtos": itens_venda_detalhado,
        "valor_total": valor_total,
        "horario": horario_venda # Adiciona o horário à venda
    })
    return venda_id, valor_total

def deletar_venda(venda_id: int):
    """Deleta uma venda, reverte o estoque e o caixa."""
    venda_a_deletar = next((v for v in st.session_state.vendas if v["id"] == venda_id), None)
    if not venda_a_deletar:
        st.warning(f"Venda com ID {venda_id} não encontrada.")
        return

    for produto, quantidade in venda_a_deletar["produtos"].items():
        if produto in st.session_state.estoque:
            st.session_state.estoque[produto] += quantidade
        else:
            st.warning(f"Produto '{produto}' da venda deletada não encontrado no estoque atual para devolução.")

    st.session_state.caixa -= venda_a_deletar["valor_total"]
    st.session_state.vendas = [v for v in st.session_state.vendas if v["id"] != venda_id]
    st.success(f"Venda ID {venda_id} deletada e estoque revertido.")

# --- Interface do Usuário (Streamlit) ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

inicializar_estado_sessao()

if st.sidebar.button("🔄 Recarregar Produtos do GitHub"):
    st.cache_data.clear()
    st.session_state.produtos_carregados = False
    inicializar_estado_sessao()
    st.rerun()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "ℹ️ Produtos e Caixa",
    "🛒 Registrar Venda",
    "📊 Vendas Realizadas",
    "📦 Estoque Atual",
    "⚙️ Gerenciar Produtos (Sessão)"
])

with tab1:
    st.subheader("Produtos Disponíveis para Venda")
    if st.session_state.get("produtos"):
        produtos_em_estoque_vis = [p for p in st.session_state.produtos if st.session_state.estoque.get(p["nome"], 0) > 0]
        if produtos_em_estoque_vis:
            df_display = pd.DataFrame(produtos_em_estoque_vis)
            df_display['valor_formatado'] = df_display['valor'].apply(lambda x: f"R${x:.2f}")
            df_display['estoque_atual'] = df_display['nome'].apply(lambda x: st.session_state.estoque.get(x,0))
            st.table(df_display[['nome', 'valor_formatado', 'estoque_atual']].rename(
                columns={'nome':'Produto', 'valor_formatado':'Valor Unitário', 'estoque_atual':'Em Estoque'}
            ))
        else:
            st.info("Nenhum produto com estoque disponível no momento.")
    else:
        st.info("Nenhum produto cadastrado ou carregado. Tente recarregar do GitHub ou adicionar manualmente (para esta sessão).")

    st.subheader("💰 Caixa")
    st.metric(label="Valor em Caixa", value=f"R${st.session_state.caixa:.2f}")

with tab2:
    st.subheader("Registrar Nova Venda")
    if not st.session_state.get("produtos"):
        st.warning("Não há produtos cadastrados para registrar uma venda. Carregue-os do GitHub ou adicione na aba 'Gerenciar Produtos'.")
    else:
        with st.form(key='registrar_venda_form'):
            produtos_para_venda = {}
            for produto in st.session_state.produtos:
                nome_produto = produto["nome"]
                estoque_atual = st.session_state.estoque.get(nome_produto, 0)
                if estoque_atual > 0:
                    quantidade = st.number_input(
                        f"{nome_produto} (Estoque: {estoque_atual}, Valor: R${produto['valor']:.2f})",
                        min_value=0,
                        max_value=estoque_atual,
                        step=1,
                        key=f"venda_{nome_produto}"
                    )
                    if quantidade > 0:
                        produtos_para_venda[nome_produto] = quantidade

            submit_venda = st.form_submit_button("Registrar Venda")

            if submit_venda:
                if produtos_para_venda:
                    venda_id, valor_total = registrar_venda(produtos_para_venda)
                    if venda_id:
                        st.success(f"Venda ID {venda_id} registrada! Valor Total: R${valor_total:.2f}")
                        st.rerun() # Para atualizar a lista de vendas e o caixa imediatamente
                else:
                    st.warning("Nenhum produto selecionado ou com quantidade maior que zero.")

with tab3:
    st.subheader("Histórico de Vendas")
    if st.session_state.vendas:
        vendas_formatadas = []
        # Ordenar vendas pela mais recente primeiro (opcional)
        vendas_ordenadas = sorted(st.session_state.vendas, key=lambda v: v['horario'], reverse=True)

        for venda in vendas_ordenadas: # Usar vendas_ordenadas aqui
            produtos_str = ", ".join([f"{nome} (Qtd: {qtd})" for nome, qtd in venda["produtos"].items()])
            horario_formatado = venda["horario"].strftime("%d/%m/%Y %H:%M:%S") # Formata o horário
            vendas_formatadas.append({
                "ID": venda["id"],
                "Horário da Venda": horario_formatado, # Adiciona coluna de horário
                "Itens Vendidos": produtos_str,
                "Valor Total (R$)": f"{venda['valor_total']:.2f}"
            })
        df_vendas = pd.DataFrame(vendas_formatadas)
        # Reordenar colunas para melhor visualização (opcional)
        if not df_vendas.empty:
            df_vendas = df_vendas[["ID", "Horário da Venda", "Itens Vendidos", "Valor Total (R$)"]]
        st.dataframe(df_vendas, use_container_width=True)

        # Prepara o DataFrame para CSV com o horário não formatado (datetime object) para melhor uso em planilhas
        vendas_para_csv = []
        for venda in st.session_state.vendas: # Pode usar as vendas ordenadas ou não, dependendo da preferência
            produtos_str_csv = "; ".join([f"{nome}: {qtd}" for nome, qtd in venda["produtos"].items()])
            vendas_para_csv.append({
                "ID_Venda": venda["id"],
                "Horario_Venda": venda["horario"], # Objeto datetime original
                "Produtos_Vendidos": produtos_str_csv,
                "Valor_Total_RS": venda["valor_total"]
            })
        df_vendas_csv = pd.DataFrame(vendas_para_csv)

        if not df_vendas_csv.empty:
            csv_export = df_vendas_csv.to_csv(index=False, sep=';').encode('utf-8-sig') # utf-8-sig para melhor compatibilidade com Excel
            st.download_button(
                label="Baixar Vendas como CSV",
                data=csv_export,
                file_name=f"historico_vendas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", # Nome do arquivo com timestamp
                mime="text/csv",
            )
        else:
            st.info("Nenhuma venda para exportar.")


        st.subheader("Deletar Venda")
        if st.session_state.vendas:
            ids_vendas = [v["id"] for v in vendas_ordenadas] # Usar vendas_ordenadas para consistência na UI
            venda_id_para_deletar = st.selectbox(
                "Selecione o ID da Venda para Deletar (mais recentes primeiro)",
                options=ids_vendas,
                index=None,
                placeholder="Escolha uma venda..."
            )
            if st.button("Confirmar Deleção da Venda", disabled=(venda_id_para_deletar is None)):
                if venda_id_para_deletar is not None:
                    deletar_venda(venda_id_para_deletar)
                    st.rerun()
        else:
            st.info("Nenhuma venda para deletar.")
    else:
        st.info("Nenhuma venda registrada ainda.")


with tab4:
    st.subheader("Estoque Atual")
    if st.session_state.estoque:
        estoque_list = []
        for produto_info in st.session_state.get("produtos", []):
            nome = produto_info["nome"]
            quantidade = st.session_state.estoque.get(nome, 0)
            valor = produto_info.get("valor", 0.0)
            estoque_list.append({"Produto": nome, "Quantidade": quantidade, "Valor Unitário": f"R${valor:.2f}"})

        if estoque_list:
            df_estoque = pd.DataFrame(estoque_list)
            st.dataframe(df_estoque, use_container_width=True)
        else:
            st.info("Nenhum produto no estoque.")
    else:
        st.info("Estoque vazio ou não inicializado. Verifique o carregamento dos produtos.")

with tab5:
    st.subheader("Gerenciar Produtos (Apenas para esta Sessão)")
    st.warning("⚠️ As alterações feitas aqui (adicionar/deletar produtos) são válidas apenas para a sessão atual do navegador e **não são salvas permanentemente** no arquivo CSV do GitHub. Para alterações permanentes, edite o arquivo CSV diretamente no GitHub e clique em 'Recarregar Produtos do GitHub' na barra lateral.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Adicionar Novo Produto")
        with st.form(key='add_produto_form'):
            nome_novo_prod = st.text_input("Nome do Produto")
            valor_novo_prod = st.number_input("Valor Unitário (R$)", min_value=0.01, step=0.50, format="%.2f")
            qtd_novo_prod = st.number_input("Quantidade Inicial", min_value=0, step=1)
            submit_add_prod = st.form_submit_button("Adicionar Produto à Sessão")

            if submit_add_prod:
                if nome_novo_prod and valor_novo_prod > 0:
                    adicionar_produto_sessao(nome_novo_prod, valor_novo_prod, qtd_novo_prod)
                    st.rerun()
                else:
                    st.error("Nome e valor do produto são obrigatórios.")
    with col2:
        st.markdown("#### Deletar Produto Existente")
        if st.session_state.get("produtos"):
            nomes_produtos_existentes = [p["nome"] for p in st.session_state.produtos]
            if nomes_produtos_existentes:
                produto_a_deletar = st.selectbox(
                    "Selecione o Produto para Deletar da Sessão",
                    options=nomes_produtos_existentes,
                    index=None,
                    placeholder="Escolha um produto..."
                    )
                if st.button("Confirmar Deleção do Produto", disabled=(produto_a_deletar is None), type="primary"):
                    if produto_a_deletar:
                        deletar_produto_sessao(produto_a_deletar)
                        st.rerun()
            else:
                st.info("Nenhum produto para deletar.")
        else:
            st.info("Nenhum produto cadastrado para deletar.")
