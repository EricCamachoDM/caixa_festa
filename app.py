import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime
import psycopg2
import psycopg2.extras # Para DictCursor
import os

# --- Configurações e Constantes ---
APP_TITLE = "Controle de Estoque e Caixa (Supabase)"
GITHUB_CSV_URL = "https://raw.githubusercontent.com/EricCamachoDM/caixa_festa/main/produtos_estoque.csv"

# --- Obter DATABASE_URL ---
try:
    DATABASE_URL = st.secrets["DATABASE_URL"]
except FileNotFoundError:
    st.error("Arquivo secrets.toml não encontrado. Configure-o em .streamlit/secrets.toml ou defina a variável de ambiente DATABASE_URL para desenvolvimento local.")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        st.stop() # Impede a execução se o BD não estiver configurado
except KeyError:
    st.error("A chave 'DATABASE_URL' não foi encontrada nos segredos do Streamlit (secrets.toml).")
    st.stop()

# --- Conexão com o Banco de Dados (Gerenciada por @st.cache_resource) ---
@st.cache_resource
def init_connection():
    """Inicializa e retorna uma conexão com o banco de dados Supabase."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError as e:
        st.error(f"Falha ao conectar ao banco de dados Supabase: {e}")
        st.error("Verifique sua string de conexão DATABASE_URL nos segredos e as configurações de rede do Supabase.")
        return None # Retorna None em caso de falha na conexão
    except Exception as e:
        st.error(f"Erro inesperado ao conectar ao banco de dados: {e}")
        return None

db_conn = init_connection() # Esta é a nossa conexão global para o script run

def run_query(query, params=None, fetch_one=False, fetch_all=False, is_dml=False):
    """Função auxiliar para executar queries de forma segura."""
    if not db_conn:
        st.error("Conexão com o banco de dados não está disponível.")
        return None if fetch_one or fetch_all else False

    try:
        with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor if not is_dml else None) as cur:
            cur.execute(query, params)
            if is_dml: # Data Manipulation Language (INSERT, UPDATE, DELETE)
                rowcount = cur.rowcount
                db_conn.commit()
                return rowcount
            elif fetch_one:
                return cur.fetchone()
            elif fetch_all:
                return cur.fetchall()
            # Se não for DML e nem fetch, apenas executa (ex: CREATE TABLE)
            db_conn.commit() # Para DDL como CREATE TABLE
            return True
    except psycopg2.Error as e:
        db_conn.rollback()
        st.error(f"Erro no banco de dados: {e}")
        # Para erros de UNIQUE constraint
        if hasattr(e, 'pgcode') and e.pgcode == '23505' and "produtos_nome_key" in str(e):
            st.toast("Erro: Já existe um produto com esse nome.", icon="⚠️")
        elif hasattr(e, 'pgcode') and e.pgcode == '23503' and "itens_venda_produto_id_fkey" in str(e): # FK violation
            st.toast("Erro: Não é possível deletar o produto, pois ele está associado a vendas registradas.", icon="⚠️")
        return None if fetch_one or fetch_all else False
    except Exception as e:
        st.error(f"Erro inesperado na query: {e}")
        return None if fetch_one or fetch_all else False


def criar_tabelas_se_nao_existirem():
    """Cria as tabelas do banco de dados se elas não existirem."""
    queries = [
        '''CREATE TABLE IF NOT EXISTS produtos (
            id SERIAL PRIMARY KEY,
            nome TEXT UNIQUE NOT NULL,
            valor REAL NOT NULL,
            quantidade_estoque INTEGER NOT NULL
        )''',
        '''CREATE TABLE IF NOT EXISTS vendas (
            id SERIAL PRIMARY KEY,
            horario TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            valor_total REAL NOT NULL
        )''',
        '''CREATE TABLE IF NOT EXISTS itens_venda (
            id SERIAL PRIMARY KEY,
            venda_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            quantidade_vendida INTEGER NOT NULL,
            valor_unitario_momento_venda REAL NOT NULL,
            FOREIGN KEY (venda_id) REFERENCES vendas(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE RESTRICT
        )'''
    ]
    for query in queries:
        run_query(query)

# --- Funções de Cache e Sincronização ---
@st.cache_data(ttl=3600)
def carregar_produtos_csv_do_github(url: str) -> pd.DataFrame | None:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
        if not all(col in df.columns for col in ["nome", "valor", "quantidade"]):
            st.error("CSV não contém colunas esperadas: 'nome', 'valor', 'quantidade'.")
            return None
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
        df['quantidade'] = pd.to_numeric(df['quantidade'], errors='coerce')
        df.dropna(subset=['nome', 'valor', 'quantidade'], inplace=True)
        return df
    except Exception as e:
        st.error(f"Erro ao carregar/processar CSV do GitHub: {e}")
        return None

def limpar_caches_de_dados():
    """Limpa o cache das funções de leitura de dados."""
    get_produtos_do_bd.clear()
    get_caixa_total_do_bd.clear()
    get_estoque_atual_do_bd.clear()
    get_vendas_do_bd.clear()

def sincronizar_csv_com_bd(url_csv: str):
    df_produtos_csv = carregar_produtos_csv_do_github(url_csv)
    if df_produtos_csv is None:
        st.error("Falha ao carregar CSV para sincronização.")
        return

    produtos_atualizados, produtos_inseridos = 0, 0
    for _, row in df_produtos_csv.iterrows():
        nome_csv, valor_csv, qtd_csv = row['nome'], row['valor'], int(row['quantidade'])
        produto_existente = run_query("SELECT id, valor, quantidade_estoque FROM produtos WHERE nome = %s", (nome_csv,), fetch_one=True)

        if produto_existente:
            if produto_existente["valor"] != valor_csv or produto_existente["quantidade_estoque"] != qtd_csv:
                if run_query("UPDATE produtos SET valor = %s, quantidade_estoque = %s WHERE nome = %s", (valor_csv, qtd_csv, nome_csv), is_dml=True):
                    produtos_atualizados += 1
        else:
            if run_query("INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (%s, %s, %s)", (nome_csv, valor_csv, qtd_csv), is_dml=True):
                produtos_inseridos += 1
    
    msg = []
    if produtos_inseridos: msg.append(f"{produtos_inseridos} novo(s) produto(s) inserido(s)")
    if produtos_atualizados: msg.append(f"{produtos_atualizados} produto(s) existente(s) atualizado(s)")
    st.success(f"Sincronização concluída. {'; '.join(msg) if msg else 'Nenhuma alteração necessária.'}")
    limpar_caches_de_dados()

# --- Funções CRUD para o Banco de Dados ---

@st.cache_data(show_spinner="Buscando produtos...")
def get_produtos_do_bd() -> list:
    rows = run_query("SELECT id, nome, valor, quantidade_estoque FROM produtos ORDER BY nome", fetch_all=True)
    return [dict(row) for row in rows] if rows else []

def adicionar_produto_bd(nome: str, valor: float, quantidade: int):
    if run_query("INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (%s, %s, %s)", (nome, valor, quantidade), is_dml=True):
        st.success(f"Produto '{nome}' adicionado.")
        limpar_caches_de_dados()
        # Não precisa de st.error aqui, run_query já trata e st.toast pode ser usado para feedback de erro de UNIQUE

def deletar_produto_bd(nome_produto: str):
    produto_info = run_query("SELECT id FROM produtos WHERE nome = %s", (nome_produto,), fetch_one=True)
    if not produto_info:
        st.warning(f"Produto '{nome_produto}' não encontrado para deleção.")
        return

    produto_id = produto_info['id']
    vendas_associadas = run_query("SELECT COUNT(*) FROM itens_venda WHERE produto_id = %s", (produto_id,), fetch_one=True)
    if vendas_associadas and vendas_associadas[0] > 0:
        # st.error já é tratado por run_query se a FK RESTRICT bloquear, ou o toast
        return

    if run_query("DELETE FROM produtos WHERE nome = %s", (nome_produto,), is_dml=True):
        st.success(f"Produto '{nome_produto}' deletado.")
        limpar_caches_de_dados()
    # else: run_query já trata o st.warning se não deletar

def registrar_venda_bd(produtos_venda_dict: dict) -> tuple[int | None, float]:
    valor_total_venda = 0.0
    itens_para_inserir_na_venda = []
    venda_id = None

    if not db_conn:
        st.error("Conexão com o banco de dados não disponível para registrar venda.")
        return None, 0.0

    try:
        with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor: # Transação manual
            # Iniciar transação
            # psycopg2 por padrão já inicia uma transação na primeira execução de comando.
            # O commit() ou rollback() finaliza.

            for nome_produto, quantidade_vendida in produtos_venda_dict.items():
                if quantidade_vendida <= 0: continue
                cursor.execute("SELECT id, nome, valor, quantidade_estoque FROM produtos WHERE nome = %s FOR UPDATE", (nome_produto,))
                produto_db = cursor.fetchone()
                if not produto_db:
                    st.error(f"Produto '{nome_produto}' não encontrado. Venda cancelada.")
                    db_conn.rollback()
                    return None, 0.0
                if produto_db["quantidade_estoque"] < quantidade_vendida:
                    st.error(f"Estoque insuficiente para '{nome_produto}'. Disponível: {produto_db['quantidade_estoque']}. Venda cancelada.")
                    db_conn.rollback()
                    return None, 0.0
                
                novo_estoque = produto_db["quantidade_estoque"] - quantidade_vendida
                cursor.execute("UPDATE produtos SET quantidade_estoque = %s WHERE id = %s", (novo_estoque, produto_db["id"]))
                
                valor_item_total = quantidade_vendida * produto_db["valor"]
                valor_total_venda += valor_item_total
                itens_para_inserir_na_venda.append({
                    "produto_id": produto_db["id"],
                    "quantidade_vendida": quantidade_vendida,
                    "valor_unitario_momento_venda": produto_db["valor"]
                })

            if not itens_para_inserir_na_venda:
                st.warning("Nenhum item válido na venda. Venda cancelada.")
                db_conn.rollback() # Importante reverter se não há itens
                return None, 0.0

            horario_atual = datetime.now()
            cursor.execute("INSERT INTO vendas (horario, valor_total) VALUES (%s, %s) RETURNING id", (horario_atual, valor_total_venda))
            venda_id_row = cursor.fetchone()
            if not venda_id_row:
                st.error("Falha ao obter ID da venda. Venda cancelada.")
                db_conn.rollback()
                return None, 0.0
            venda_id = venda_id_row['id']

            for item in itens_para_inserir_na_venda:
                cursor.execute(
                    "INSERT INTO itens_venda (venda_id, produto_id, quantidade_vendida, valor_unitario_momento_venda) VALUES (%s, %s, %s, %s)",
                    (venda_id, item["produto_id"], item["quantidade_vendida"], item["valor_unitario_momento_venda"])
                )
            db_conn.commit() # Commit da transação
            limpar_caches_de_dados()
            return venda_id, valor_total_venda
    except psycopg2.Error as e:
        db_conn.rollback()
        st.error(f"Erro de banco de dados ao registrar venda: {e}")
        return None, 0.0
    except Exception as e: # Capturar outros erros que podem ocorrer
        db_conn.rollback()
        st.error(f"Erro inesperado ao registrar venda: {e}")
        return None, 0.0


@st.cache_data(show_spinner="Buscando histórico de vendas...")
def get_vendas_do_bd() -> list:
    query = """
        SELECT v.id AS venda_id, v.horario, v.valor_total,
               STRING_AGG(p.nome || ' (Qtd: ' || iv.quantidade_vendida || ', Vlr Unit: R$' || TO_CHAR(iv.valor_unitario_momento_venda, 'FM999999990.00') || ')', '; ') AS produtos_detalhados
        FROM vendas v
        LEFT JOIN itens_venda iv ON v.id = iv.venda_id
        LEFT JOIN produtos p ON iv.produto_id = p.id
        GROUP BY v.id, v.horario, v.valor_total ORDER BY v.horario DESC
    """
    rows = run_query(query, fetch_all=True)
    return [dict(row) for row in rows] if rows else []

def deletar_venda_bd(venda_id_para_deletar: int):
    if not db_conn: return
    try:
        with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute("SELECT produto_id, quantidade_vendida FROM itens_venda WHERE venda_id = %s", (venda_id_para_deletar,))
            itens_da_venda = cursor.fetchall()
            if not itens_da_venda:
                st.warning(f"Venda ID {venda_id_para_deletar} não encontrada ou sem itens. Nada a reverter.")
                db_conn.rollback() # Importante se a transação foi implicitamente iniciada
                return

            for item in itens_da_venda:
                cursor.execute("UPDATE produtos SET quantidade_estoque = quantidade_estoque + %s WHERE id = %s", (item["quantidade_vendida"], item["produto_id"]))
            
            # ON DELETE CASCADE na FK de itens_venda para vendas deve cuidar da deleção dos itens.
            # Se não, você precisaria de: cursor.execute("DELETE FROM itens_venda WHERE venda_id = %s", (venda_id_para_deletar,))
            cursor.execute("DELETE FROM vendas WHERE id = %s", (venda_id_para_deletar,))
        db_conn.commit()
        st.success(f"Venda ID {venda_id_para_deletar} deletada e estoque revertido.")
        limpar_caches_de_dados()
    except psycopg2.Error as e:
        db_conn.rollback()
        st.error(f"Erro de BD ao deletar venda ID {venda_id_para_deletar}: {e}")
    except Exception as e:
        db_conn.rollback()
        st.error(f"Erro inesperado ao deletar venda: {e}")


@st.cache_data(show_spinner="Calculando caixa...")
def get_caixa_total_do_bd() -> float:
    resultado = run_query("SELECT SUM(valor_total) FROM vendas", fetch_one=True)
    return resultado[0] if resultado and resultado[0] is not None else 0.0

@st.cache_data(show_spinner="Verificando estoque...")
def get_estoque_atual_do_bd() -> pd.DataFrame:
    rows = run_query("SELECT nome, quantidade_estoque, valor FROM produtos WHERE quantidade_estoque >= 0 ORDER BY nome", fetch_all=True)
    if rows:
        estoque_list = [{"Produto": row["nome"], "Quantidade": row["quantidade_estoque"], "Valor Unitário": f"R${row['valor']:.2f}"} for row in rows]
        return pd.DataFrame(estoque_list)
    return pd.DataFrame()


# --- Interface Streamlit (UI) ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

if not db_conn:
    st.error("A aplicação não pode iniciar: falha crítica na conexão com o banco de dados.")
    st.stop() # Interrompe a execução do script se não houver conexão
else:
    criar_tabelas_se_nao_existirem() # Chama a função que usa a conexão global

    if st.sidebar.button("🔄 Sincronizar Produtos do CSV com o Banco de Dados"):
        sincronizar_csv_com_bd(GITHUB_CSV_URL)
        st.rerun()

    # Verificar se a tabela de produtos está vazia
    count_result = run_query("SELECT COUNT(*) FROM produtos", fetch_one=True)
    if count_result and count_result[0] == 0:
        st.sidebar.info("O banco de dados de produtos parece estar vazio. "
                        "Clique em 'Sincronizar Produtos...' para carregar.")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "ℹ️ Produtos e Caixa", "🛒 Registrar Venda", "📊 Vendas Realizadas",
        "📦 Estoque Atual", "⚙️ Gerenciar Produtos (BD)"
    ])

    with tab1:
        st.subheader("Produtos Disponíveis para Venda")
        produtos_bd_tab1 = get_produtos_do_bd()
        produtos_em_estoque_vis = [p for p in produtos_bd_tab1 if p["quantidade_estoque"] > 0]
        if produtos_em_estoque_vis:
            df_display_tab1 = pd.DataFrame(produtos_em_estoque_vis)
            df_display_tab1['valor_formatado'] = df_display_tab1['valor'].apply(lambda x: f"R${x:.2f}")
            st.table(df_display_tab1[['nome', 'valor_formatado', 'quantidade_estoque']].rename(
                columns={'nome':'Produto', 'valor_formatado':'Valor Unitário', 'quantidade_estoque':'Em Estoque'}
            ))
        elif not produtos_bd_tab1:
            st.info("Nenhum produto cadastrado no banco de dados.")
        else:
            st.info("Nenhum produto com estoque disponível no momento.")

        st.subheader("💰 Caixa")
        caixa_total_bd = get_caixa_total_do_bd()
        st.metric(label="Valor em Caixa", value=f"R${caixa_total_bd:.2f}")

    with tab2:
        st.subheader("Registrar Nova Venda")
        produtos_para_venda_bd_tab2 = get_produtos_do_bd()
        if not produtos_para_venda_bd_tab2:
            st.warning("Não há produtos cadastrados para registrar uma venda.")
        else:
            with st.form(key='registrar_venda_form_bd'): # Chave única
                input_produtos_para_venda_dict = {}
                for produto_info in produtos_para_venda_bd_tab2:
                    if produto_info["quantidade_estoque"] > 0:
                        quantidade_selecionada = st.number_input(
                            f"{produto_info['nome']} (Estoque: {produto_info['quantidade_estoque']}, "
                            f"Valor: R${produto_info['valor']:.2f})",
                            min_value=0, max_value=produto_info["quantidade_estoque"], step=1,
                            key=f"venda_bd_form_{produto_info['nome']}" # Chave única
                        )
                        if quantidade_selecionada > 0:
                            input_produtos_para_venda_dict[produto_info['nome']] = quantidade_selecionada
                submit_venda_bd = st.form_submit_button("Registrar Venda")
                if submit_venda_bd:
                    if input_produtos_para_venda_dict:
                        venda_id_registrada, valor_total_registrado = registrar_venda_bd(
                            input_produtos_para_venda_dict
                        )
                        if venda_id_registrada is not None: # Verificar se não é None
                            st.success(f"Venda ID {venda_id_registrada} registrada! Valor: R${valor_total_registrado:.2f}")
                            st.rerun()
                        # else: O erro já foi mostrado dentro de registrar_venda_bd
                    else:
                        st.warning("Nenhum produto selecionado ou quantidade inválida.")

    with tab3:
        st.subheader("Histórico de Vendas")
        vendas_registradas_bd = get_vendas_do_bd()
        if vendas_registradas_bd:
            vendas_formatadas_para_display = []
            for venda_row_dict in vendas_registradas_bd:
                horario_venda = venda_row_dict["horario"]
                horario_str = horario_venda.strftime("%d/%m/%Y %H:%M:%S") if isinstance(horario_venda, datetime) else str(horario_venda)
                vendas_formatadas_para_display.append({
                    "ID": venda_row_dict["venda_id"],
                    "Horário da Venda": horario_str,
                    "Itens Vendidos": venda_row_dict.get("produtos_detalhados", "N/A"),
                    "Valor Total (R$)": f"{venda_row_dict['valor_total']:.2f}"
                })
            df_vendas_para_display = pd.DataFrame(vendas_formatadas_para_display)
            st.dataframe(df_vendas_para_display, use_container_width=True)

            if not df_vendas_para_display.empty:
                csv_export_data = df_vendas_para_display.to_csv(index=False, sep=';').encode('utf-8-sig')
                st.download_button(
                    label="Baixar Histórico de Vendas como CSV", data=csv_export_data,
                    file_name=f"historico_vendas_db_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", # Removido _supa
                    mime="text/csv",
                )
            st.subheader("Deletar Venda Registrada")
            ids_vendas_existentes = [v["venda_id"] for v in vendas_registradas_bd]
            if ids_vendas_existentes:
                venda_id_del = st.selectbox("ID da Venda para Deletar", options=ids_vendas_existentes, index=None, key="del_venda_key") # Chave única
                if st.button("Confirmar Deleção da Venda", disabled=(venda_id_del is None), key="btn_del_venda_key"): # Chave única
                    if venda_id_del is not None:
                        deletar_venda_bd(venda_id_del)
                        st.rerun()
        else:
            st.info("Nenhuma venda registrada no banco de dados.")

    with tab4:
        st.subheader("Estoque Atual de Produtos")
        df_estoque_atual_bd = get_estoque_atual_do_bd()
        if not df_estoque_atual_bd.empty:
            st.dataframe(df_estoque_atual_bd, use_container_width=True)
        else:
            st.info("Nenhum produto cadastrado no estoque.")

    with tab5:
        st.subheader("Gerenciar Produtos (Persistente no Banco de Dados)")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### Adicionar Novo Produto ao BD")
            with st.form(key='add_produto_bd_form_tab5'): # Chave única
                nome_novo = st.text_input("Nome do Produto")
                valor_novo = st.number_input("Valor Unitário (R$)", min_value=0.01, step=0.01, format="%.2f")
                qtd_nova = st.number_input("Qtd Inicial em Estoque", min_value=0, step=1)
                submit_add = st.form_submit_button("Adicionar Produto ao BD")
                if submit_add:
                    if nome_novo and valor_novo > 0:
                        adicionar_produto_bd(nome_novo, valor_novo, qtd_nova)
                        st.rerun()
                    else:
                        st.error("Nome e valor (>0) são obrigatórios.")
        with col2:
            st.markdown("#### Deletar Produto Existente do BD")
            produtos_atuais_del_bd = get_produtos_do_bd()
            if produtos_atuais_del_bd:
                nomes_produtos_del = [p["nome"] for p in produtos_atuais_del_bd]
                if nomes_produtos_del:
                    produto_del = st.selectbox("Produto para Deletar do BD", options=nomes_produtos_del, index=None, key="del_prod_key") # Chave única
                    if st.button("Confirmar Deleção do Produto", disabled=(produto_del is None), key="btn_del_prod_key"): # Chave única
                         if produto_del is not None:
                            deletar_produto_bd(produto_del)
                            st.rerun()
                else:
                    st.info("Nenhum produto para deletar.")
            else:
                st.info("Nenhum produto cadastrado.")
