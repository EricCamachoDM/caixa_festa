import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime
import sqlite3
import os 

# --- Configurações e Constantes ---
APP_TITLE = "Controle de Estoque e Caixa (DB Compartilhado)"
GITHUB_CSV_URL = "https://raw.githubusercontent.com/EricCamachoDM/caixa_festa/refs/heads/main/produtos_estoque.csv"
DATABASE_FILE = "festa_macarronada.db" 

# --- Funções de Banco de Dados ---

def conectar_bd():
    """Conecta ao banco de dados SQLite e retorna a conexão."""
    conn = sqlite3.connect(DATABASE_FILE, timeout=10) # Timeout para concorrência
    conn.row_factory = sqlite3.Row # Permite acessar colunas pelo nome
    return conn

def criar_tabelas_se_nao_existirem(conn: sqlite3.Connection):
    """Cria as tabelas do banco de dados se elas não existirem."""
    cursor = conn.cursor()
    # Tabela de Produtos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            valor REAL NOT NULL,
            quantidade_estoque INTEGER NOT NULL
        )
    ''')
    # Tabela de Vendas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            horario TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            valor_total REAL NOT NULL
        )
    ''')
    # Tabela de Itens da Venda (relação muitos-para-muitos)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS itens_venda (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venda_id INTEGER NOT NULL,
            produto_id INTEGER NOT NULL,
            quantidade_vendida INTEGER NOT NULL,
            valor_unitario_momento_venda REAL NOT NULL,
            FOREIGN KEY (venda_id) REFERENCES vendas(id) ON DELETE CASCADE,
            FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE RESTRICT
        )
    ''')
    conn.commit()

@st.cache_data(ttl=3600) # Cache do CSV por 1 hora para não buscar toda hora no GitHub
def carregar_produtos_csv_do_github(url: str) -> pd.DataFrame | None:
    """Carrega os dados dos produtos de um arquivo CSV no GitHub."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        df = pd.read_csv(StringIO(response.text))
        # Validação básica das colunas esperadas
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
    except Exception as e:
        st.error(f"Erro ao processar o arquivo CSV: {e}")
        return None

def sincronizar_csv_com_bd(conn: sqlite3.Connection, url_csv: str):
    """
    Carrega produtos do CSV e os insere/atualiza no banco de dados.
    Se um produto do CSV já existe no BD (pelo nome), atualiza seu valor e quantidade_estoque.
    Se não existe, insere como novo.
    Não remove produtos do BD que não estão mais no CSV (para manter histórico).
    """
    df_produtos_csv = carregar_produtos_csv_do_github(url_csv)
    if df_produtos_csv is None:
        st.error("Não foi possível carregar produtos do CSV para sincronização com o BD.")
        return

    cursor = conn.cursor()
    produtos_atualizados = 0
    produtos_inseridos = 0

    for _, row in df_produtos_csv.iterrows():
        nome_produto_csv = row['nome']
        valor_produto_csv = row['valor']
        quantidade_produto_csv = int(row['quantidade']) # Garantir que é int

        cursor.execute("SELECT id, valor, quantidade_estoque FROM produtos WHERE nome = ?", (nome_produto_csv,))
        produto_existente = cursor.fetchone()

        if produto_existente:
            # Atualiza se valor ou quantidade_inicial (do CSV) for diferente
            # Não vamos zerar o estoque atual, apenas a "quantidade de referência" do CSV
            if produto_existente["valor"] != valor_produto_csv or produto_existente["quantidade_estoque"] != quantidade_produto_csv:
                cursor.execute(
                    "UPDATE produtos SET valor = ?, quantidade_estoque = ? WHERE nome = ?",
                    (valor_produto_csv, quantidade_produto_csv, nome_produto_csv)
                )
                produtos_atualizados +=1
        else:
            # Insere novo produto
            cursor.execute(
                "INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (?, ?, ?)",
                (nome_produto_csv, valor_produto_csv, quantidade_produto_csv)
            )
            produtos_inseridos +=1
    conn.commit()
    msg = []
    if produtos_inseridos > 0: msg.append(f"{produtos_inseridos} novo(s) produto(s) inserido(s)")
    if produtos_atualizados > 0: msg.append(f"{produtos_atualizados} produto(s) existente(s) atualizado(s)")
    if not msg: msg.append("Nenhuma alteração nos produtos do BD pela sincronização.")

    st.success(f"Sincronização de produtos do CSV com o BD concluída. {'; '.join(msg)}")
    # Limpar caches de funções que leem do BD para forçar releitura
    get_produtos_do_bd.clear()
    get_caixa_total_do_bd.clear()
    get_estoque_atual_do_bd.clear()
    get_vendas_do_bd.clear()


# --- Funções de Negócio (Adaptadas para Banco de Dados) ---
@st.cache_data(show_spinner="Buscando produtos...") # Adiciona cache para leituras frequentes
def get_produtos_do_bd(conn: sqlite3.Connection) -> list:
    """Retorna uma lista de todos os produtos do BD."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, valor, quantidade_estoque FROM produtos ORDER BY nome")
    return [dict(row) for row in cursor.fetchall()]

def adicionar_produto_bd(conn: sqlite3.Connection, nome: str, valor: float, quantidade: int):
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO produtos (nome, valor, quantidade_estoque) VALUES (?, ?, ?)",
            (nome, valor, quantidade)
        )
        conn.commit()
        st.success(f"Produto '{nome}' adicionado ao banco de dados.")
        get_produtos_do_bd.clear() # Limpa cache
    except sqlite3.IntegrityError: # Erro se o nome do produto já existir (UNIQUE constraint)
        st.error(f"Produto '{nome}' já existe no banco de dados.")
    except Exception as e:
        st.error(f"Erro ao adicionar produto: {e}")
        conn.rollback() # Desfaz a transação em caso de erro

def deletar_produto_bd(conn: sqlite3.Connection, nome_produto: str):
    cursor = conn.cursor()
    try:
        # Primeiro, obter o ID do produto para verificar se está em itens_venda
        cursor.execute("SELECT id FROM produtos WHERE nome = ?", (nome_produto,))
        produto_info = cursor.fetchone()
        if not produto_info:
            st.error(f"Produto '{nome_produto}' não encontrado para deleção.")
            return

        produto_id = produto_info['id']

        # Verificar se o produto está em alguma venda (itens_venda)
        # A FK com ON DELETE RESTRICT deve impedir isso, mas uma verificação manual é boa
        cursor.execute("SELECT COUNT(*) FROM itens_venda WHERE produto_id = ?", (produto_id,))
        if cursor.fetchone()[0] > 0:
            st.error(f"Não é possível deletar o produto '{nome_produto}', pois ele está associado a vendas registradas. "
                       "Considere marcar como 'indisponível' ou alterar o estoque para zero.")
            return

        cursor.execute("DELETE FROM produtos WHERE nome = ?", (nome_produto,))
        conn.commit()
        if cursor.rowcount > 0:
            st.success(f"Produto '{nome_produto}' deletado do banco de dados.")
            get_produtos_do_bd.clear() # Limpa cache
        else: # Caso raro, se o produto foi deletado entre a seleção e o clique
            st.warning(f"Produto '{nome_produto}' não encontrado para deleção (ou já deletado).")
    except sqlite3.IntegrityError as e: # Pode ocorrer se houver FKs não tratadas
        st.error(f"Erro de integridade ao tentar deletar '{nome_produto}'. Detalhe: {e}")
        conn.rollback()
    except Exception as e:
        st.error(f"Erro ao deletar produto: {e}")
        conn.rollback()


def registrar_venda_bd(conn: sqlite3.Connection, produtos_venda_dict: dict) -> tuple[int | None, float]:
    """Registra uma venda no banco de dados, atualizando o estoque dos produtos."""
    if not produtos_venda_dict:
        st.warning("Nenhum produto selecionado para a venda.")
        return None, 0.0

    cursor = conn.cursor()
    valor_total_venda = 0.0
    itens_para_inserir_na_venda = []

    try:
        conn.execute("BEGIN TRANSACTION") # Iniciar transação para garantir atomicidade

        for nome_produto, quantidade_vendida in produtos_venda_dict.items():
            if quantidade_vendida <= 0:
                continue

            # Re-buscar produto para ter certeza do valor e estoque atuais dentro da transação
            cursor.execute("SELECT id, nome, valor, quantidade_estoque FROM produtos WHERE nome = ?", (nome_produto,))
            produto_db = cursor.fetchone()

            if not produto_db:
                st.error(f"Produto '{nome_produto}' não encontrado no BD durante a transação. Venda cancelada.")
                conn.rollback()
                return None, 0.0

            if produto_db["quantidade_estoque"] < quantidade_vendida:
                st.error(f"Estoque insuficiente para '{nome_produto}'. Disponível: {produto_db['quantidade_estoque']}. Venda cancelada.")
                conn.rollback()
                return None, 0.0

            # Atualizar estoque do produto
            novo_estoque = produto_db["quantidade_estoque"] - quantidade_vendida
            cursor.execute(
                "UPDATE produtos SET quantidade_estoque = ? WHERE id = ?",
                (novo_estoque, produto_db["id"])
            )

            # Calcular valor e preparar item para tabela itens_venda
            valor_item_total = quantidade_vendida * produto_db["valor"]
            valor_total_venda += valor_item_total
            itens_para_inserir_na_venda.append({
                "produto_id": produto_db["id"],
                "quantidade_vendida": quantidade_vendida,
                "valor_unitario_momento_venda": produto_db["valor"] # Preço no momento da venda
            })

        if not itens_para_inserir_na_venda: # Se nenhum item válido foi processado
            st.warning("Nenhum item válido na venda. Venda cancelada.")
            conn.rollback()
            return None, 0.0

        # Inserir o registro da venda principal
        horario_atual = datetime.now()
        cursor.execute(
            "INSERT INTO vendas (horario, valor_total) VALUES (?, ?)",
            (horario_atual, valor_total_venda)
        )
        venda_id = cursor.lastrowid # Pega o ID da venda recém-inserida

        # Inserir os itens da venda na tabela itens_venda
        for item in itens_para_inserir_na_venda:
            cursor.execute('''
                INSERT INTO itens_venda (venda_id, produto_id, quantidade_vendida, valor_unitario_momento_venda)
                VALUES (?, ?, ?, ?)
            ''', (venda_id, item["produto_id"], item["quantidade_vendida"], item["valor_unitario_momento_venda"]))

        conn.commit() # Finalizar transação com sucesso
        # Limpar caches
        get_produtos_do_bd.clear()
        get_caixa_total_do_bd.clear()
        get_estoque_atual_do_bd.clear()
        get_vendas_do_bd.clear()
        return venda_id, valor_total_venda

    except Exception as e:
        conn.rollback() # Reverter todas as alterações em caso de qualquer erro
        st.error(f"Erro crítico ao registrar venda: {e}. A transação foi revertida.")
        return None, 0.0

@st.cache_data(show_spinner="Buscando histórico de vendas...")
def get_vendas_do_bd(conn: sqlite3.Connection) -> list:
    """Retorna uma lista de todas as vendas do BD, com detalhes dos produtos."""
    cursor = conn.cursor()
    query = """
        SELECT
            v.id AS venda_id,
            v.horario,
            v.valor_total,
            GROUP_CONCAT(p.nome || ' (Qtd: ' || iv.quantidade_vendida || ', Vlr Unit: R$' || printf("%.2f", iv.valor_unitario_momento_venda) || ')', '; ') AS produtos_detalhados
        FROM vendas v
        LEFT JOIN itens_venda iv ON v.id = iv.venda_id  -- LEFT JOIN para caso de venda sem itens (não deveria acontecer)
        LEFT JOIN produtos p ON iv.produto_id = p.id
        GROUP BY v.id, v.horario, v.valor_total
        ORDER BY v.horario DESC
    """
    cursor.execute(query)
    vendas = []
    for row_dict in (dict(row) for row in cursor.fetchall()):
        # Convertendo string de horário para datetime object se necessário
        if isinstance(row_dict['horario'], str):
            try:
                row_dict['horario'] = datetime.fromisoformat(row_dict['horario'])
            except ValueError: # Tentar outro formato se fromisoformat falhar
                 try:
                    row_dict['horario'] = datetime.strptime(row_dict['horario'], '%Y-%m-%d %H:%M:%S.%f') # formato comum do sqlite
                 except ValueError:
                    pass # Deixa como string se não conseguir converter

        vendas.append(row_dict)
    return vendas


def deletar_venda_bd(conn: sqlite3.Connection, venda_id_para_deletar: int):
    """Deleta uma venda do BD e reverte o estoque dos produtos envolvidos."""
    cursor = conn.cursor()
    try:
        conn.execute("BEGIN TRANSACTION")

        # Buscar os itens da venda que será deletada para saber o que reverter no estoque
        cursor.execute("""
            SELECT produto_id, quantidade_vendida
            FROM itens_venda
            WHERE venda_id = ?
        """, (venda_id_para_deletar,))
        itens_da_venda = cursor.fetchall()

        if not itens_da_venda:
            st.warning(f"Venda ID {venda_id_para_deletar} não encontrada ou não possui itens. Nada a reverter.")
            conn.rollback()
            return

        # Reverter o estoque para cada item da venda
        for item in itens_da_venda:
            cursor.execute(
                "UPDATE produtos SET quantidade_estoque = quantidade_estoque + ? WHERE id = ?",
                (item["quantidade_vendida"], item["produto_id"])
            )

        # Deletar os itens da venda e depois a venda principal
        # A FK com ON DELETE CASCADE na tabela itens_venda deve deletar os itens automaticamente ao deletar a venda
        # cursor.execute("DELETE FROM itens_venda WHERE venda_id = ?", (venda_id_para_deletar,))
        cursor.execute("DELETE FROM vendas WHERE id = ?", (venda_id_para_deletar,))

        conn.commit()
        st.success(f"Venda ID {venda_id_para_deletar} deletada e estoque dos produtos revertido.")
        # Limpar caches
        get_produtos_do_bd.clear()
        get_caixa_total_do_bd.clear()
        get_estoque_atual_do_bd.clear()
        get_vendas_do_bd.clear()

    except Exception as e:
        conn.rollback()
        st.error(f"Erro ao deletar venda ID {venda_id_para_deletar}: {e}. Transação revertida.")

@st.cache_data(show_spinner="Calculando caixa...")
def get_caixa_total_do_bd(conn: sqlite3.Connection) -> float:
    """Calcula o valor total em caixa (soma de todas as vendas registradas no BD)."""
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(valor_total) FROM vendas")
    resultado = cursor.fetchone()
    return resultado[0] if resultado and resultado[0] is not None else 0.0

@st.cache_data(show_spinner="Verificando estoque...")
def get_estoque_atual_do_bd(conn: sqlite3.Connection) -> pd.DataFrame:
    """Retorna um DataFrame com o nome do produto, sua quantidade em estoque e valor."""
    cursor = conn.cursor()
    cursor.execute("SELECT nome, quantidade_estoque, valor FROM produtos WHERE quantidade_estoque >= 0 ORDER BY nome") # >=0 para incluir zerados
    estoque_list = [
        {"Produto": row["nome"], "Quantidade": row["quantidade_estoque"], "Valor Unitário": f"R${row['valor']:.2f}"}
        for row in cursor.fetchall()
    ]
    return pd.DataFrame(estoque_list)


# --- Interface Streamlit (UI) ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# Conexão com o BD e criação/verificação de tabelas
# É importante que a conexão seja estabelecida uma vez por script run e passada para as funções
db_connection = conectar_bd()
criar_tabelas_se_nao_existirem(db_connection)

# Botão para sincronizar/carregar produtos do CSV para o BD
if st.sidebar.button("🔄 Sincronizar Produtos do CSV com o Banco de Dados"):
    sincronizar_csv_com_bd(db_connection, GITHUB_CSV_URL)
    st.rerun() # Recarrega a página para refletir mudanças no BD

# Verificar se a tabela de produtos está vazia e sugerir sincronização
cursor_check = db_connection.cursor()
cursor_check.execute("SELECT COUNT(*) FROM produtos")
if cursor_check.fetchone()[0] == 0:
    st.sidebar.info("O banco de dados de produtos parece estar vazio. "
                    "Clique em 'Sincronizar Produtos do CSV com o Banco de Dados' para carregar a lista inicial de produtos.")
cursor_check.close()


tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "ℹ️ Produtos e Caixa",
    "🛒 Registrar Venda",
    "📊 Vendas Realizadas",
    "📦 Estoque Atual",
    "⚙️ Gerenciar Produtos (BD)"
])

with tab1:
    st.subheader("Produtos Disponíveis para Venda")
    produtos_bd_tab1 = get_produtos_do_bd(db_connection) # Busca dados frescos do BD
    produtos_em_estoque_vis = [p for p in produtos_bd_tab1 if p["quantidade_estoque"] > 0]

    if produtos_em_estoque_vis:
        df_display_tab1 = pd.DataFrame(produtos_em_estoque_vis)
        df_display_tab1['valor_formatado'] = df_display_tab1['valor'].apply(lambda x: f"R${x:.2f}")
        st.table(df_display_tab1[['nome', 'valor_formatado', 'quantidade_estoque']].rename(
            columns={'nome':'Produto', 'valor_formatado':'Valor Unitário', 'quantidade_estoque':'Em Estoque'}
        ))
    elif not produtos_bd_tab1: # Se a lista de produtos do BD estiver vazia
        st.info("Nenhum produto cadastrado no banco de dados. Use a sincronização ou adicione na aba 'Gerenciar Produtos'.")
    else: # Se há produtos, mas todos sem estoque
        st.info("Nenhum produto com estoque disponível no momento.")

    st.subheader("💰 Caixa")
    caixa_total_bd = get_caixa_total_do_bd(db_connection) # Busca dado fresco do BD
    st.metric(label="Valor em Caixa", value=f"R${caixa_total_bd:.2f}")

with tab2:
    st.subheader("Registrar Nova Venda")
    produtos_para_venda_bd_tab2 = get_produtos_do_bd(db_connection)

    if not produtos_para_venda_bd_tab2:
        st.warning("Não há produtos cadastrados para registrar uma venda. "
                   "Sincronize ou adicione produtos na aba 'Gerenciar Produtos'.")
    else:
        with st.form(key='registrar_venda_form_bd'):
            input_produtos_para_venda_dict = {}
            for produto_info in produtos_para_venda_bd_tab2:
                # Só mostrar input para produtos com estoque
                if produto_info["quantidade_estoque"] > 0:
                    quantidade_selecionada = st.number_input(
                        f"{produto_info['nome']} (Estoque: {produto_info['quantidade_estoque']}, "
                        f"Valor: R${produto_info['valor']:.2f})",
                        min_value=0,
                        max_value=produto_info["quantidade_estoque"], # Limita ao estoque atual
                        step=1,
                        key=f"venda_bd_{produto_info['nome']}" # Chave única para o input
                    )
                    if quantidade_selecionada > 0:
                        input_produtos_para_venda_dict[produto_info['nome']] = quantidade_selecionada
                # Opcional: mostrar produtos esgotados
                # else:
                #     st.caption(f"{produto_info['nome']} - ESGOTADO")


            submit_venda_bd = st.form_submit_button("Registrar Venda")

            if submit_venda_bd:
                if input_produtos_para_venda_dict:
                    venda_id_registrada, valor_total_registrado = registrar_venda_bd(
                        db_connection, input_produtos_para_venda_dict
                    )
                    if venda_id_registrada: # Se a venda foi bem-sucedida
                        st.success(f"Venda ID {venda_id_registrada} registrada com sucesso! "
                                   f"Valor Total: R${valor_total_registrado:.2f}")
                        st.rerun() # Recarrega para atualizar caixa, estoque, etc.
                else:
                    st.warning("Nenhum produto selecionado ou quantidade inválida.")

with tab3:
    st.subheader("Histórico de Vendas")
    vendas_registradas_bd = get_vendas_do_bd(db_connection)

    if vendas_registradas_bd:
        vendas_formatadas_para_display = []
        for venda_row_dict in vendas_registradas_bd:
            horario_venda = venda_row_dict["horario"]
            # Garantir que horário é datetime antes de formatar
            if isinstance(horario_venda, datetime):
                horario_str = horario_venda.strftime("%d/%m/%Y %H:%M:%S")
            elif isinstance(horario_venda, str): # Se já for string (ex: do GROUP_CONCAT ou falha na conversão)
                 horario_str = horario_venda # Usar como está ou tentar re-parsear
            else:
                horario_str = "N/A"

            vendas_formatadas_para_display.append({
                "ID": venda_row_dict["venda_id"],
                "Horário da Venda": horario_str,
                "Itens Vendidos": venda_row_dict["produtos_detalhados"] if venda_row_dict["produtos_detalhados"] else "Nenhum item (verificar dados)",
                "Valor Total (R$)": f"{venda_row_dict['valor_total']:.2f}"
            })
        df_vendas_para_display = pd.DataFrame(vendas_formatadas_para_display)
        st.dataframe(df_vendas_para_display, use_container_width=True)

        # Exportação para CSV
        if not df_vendas_para_display.empty:
            csv_export_data = df_vendas_para_display.to_csv(index=False, sep=';').encode('utf-8-sig')
            st.download_button(
                label="Baixar Histórico de Vendas como CSV",
                data=csv_export_data,
                file_name=f"historico_vendas_db_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )

        st.subheader("Deletar Venda Registrada")
        ids_vendas_existentes_para_delecao = [v["venda_id"] for v in vendas_registradas_bd]
        if ids_vendas_existentes_para_delecao:
            venda_id_selecionada_para_deletar = st.selectbox(
                "Selecione o ID da Venda para Deletar",
                options=ids_vendas_existentes_para_delecao,
                index=None, # Não pré-selecionar
                placeholder="Escolha uma venda..."
            )
            if st.button("Confirmar Deleção da Venda", disabled=(venda_id_selecionada_para_deletar is None)):
                if venda_id_selecionada_para_deletar is not None:
                    deletar_venda_bd(db_connection, venda_id_selecionada_para_deletar)
                    st.rerun() # Recarrega para atualizar tudo
        else: # Deveria ser coberto por if vendas_registradas_bd
            st.info("Nenhuma venda registrada para deletar.")
    else:
        st.info("Nenhuma venda registrada no banco de dados ainda.")


with tab4:
    st.subheader("Estoque Atual de Produtos")
    df_estoque_atual_bd = get_estoque_atual_do_bd(db_connection)
    if not df_estoque_atual_bd.empty:
        st.dataframe(df_estoque_atual_bd, use_container_width=True)
    else:
        st.info("Nenhum produto cadastrado no estoque ou todos os produtos estão com estoque zerado.")

with tab5:
    st.subheader("Gerenciar Produtos (Persistente no Banco de Dados)")
    st.info("As alterações feitas aqui (adicionar/deletar produtos) são salvas diretamente no banco de dados compartilhado.")

    col1_gerenciar, col2_gerenciar = st.columns(2)
    with col1_gerenciar:
        st.markdown("#### Adicionar Novo Produto ao BD")
        with st.form(key='add_produto_bd_form_tab5'):
            nome_novo_prod_bd_input = st.text_input("Nome do Produto")
            valor_novo_prod_bd_input = st.number_input("Valor Unitário (R$)", min_value=0.01, step=0.01, format="%.2f")
            qtd_novo_prod_bd_input = st.number_input("Quantidade Inicial em Estoque", min_value=0, step=1)
            submit_add_prod_bd_button = st.form_submit_button("Adicionar Produto ao BD")

            if submit_add_prod_bd_button:
                if nome_novo_prod_bd_input and valor_novo_prod_bd_input > 0: # Quantidade pode ser 0
                    adicionar_produto_bd(
                        db_connection, nome_novo_prod_bd_input,
                        valor_novo_prod_bd_input, qtd_novo_prod_bd_input
                    )
                    st.rerun() # Recarrega
                else:
                    st.error("Nome do produto e valor unitário (maior que zero) são obrigatórios.")
    with col2_gerenciar:
        st.markdown("#### Deletar Produto Existente do BD")
        produtos_atuais_para_delecao_bd = get_produtos_do_bd(db_connection)
        if produtos_atuais_para_delecao_bd:
            nomes_produtos_bd_para_select = [p["nome"] for p in produtos_atuais_para_delecao_bd]
            if nomes_produtos_bd_para_select:
                produto_a_deletar_bd_input = st.selectbox(
                    "Selecione o Produto para Deletar do BD",
                    options=nomes_produtos_bd_para_select,
                    index=None,
                    placeholder="Escolha um produto..."
                )
                if st.button("Confirmar Deleção do Produto do BD", disabled=(produto_a_deletar_bd_input is None), type="primary"):
                    if produto_a_deletar_bd_input:
                        deletar_produto_bd(db_connection, produto_a_deletar_bd_input)
                        st.rerun() # Recarrega
            else:
                st.info("Nenhum produto cadastrado para deletar.")
        else:
            st.info("Nenhum produto cadastrado no banco de dados.")
