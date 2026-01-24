import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v9.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Otimizador Intra-Turma (V9)")
st.markdown("""
**Estratégia V9.0:**
1.  **Troca de Dia:** Se o dia preferido falhar, tenta outros dias livres do docente.
2.  **Inversão de Bloco:** UCs curtas (20h/40h) que falham no início são jogadas para o fim do semestre.
3.  **Deslocamento:** Tenta iniciar em qualquer semana viável (Sliding Window).
""")

# --- DADOS DE CONTEXTO ---
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS_DISPONIVEIS = [f"Sala {i}" for i in range(1, 13) if i != 6]

# --- FUNÇÕES ---
def gerar_template():
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

def converter_df_para_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# --- MOTOR V9 ---
class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes_restricoes):
        self.demandas = df_demandas.fillna("")
        self.restricoes_docentes = df_docentes_restricoes.fillna("")
        self.grade = []
        self.log_erros = []
        self.ocupacao = {} # Chave: "RECURSO|DIA|TURNO" -> Set de semanas

    def verificar_bloqueio_docente(self, docente, dia, turno):
        """Verifica se o docente tem bloqueio fixo (ex: Não trabalha 4ª Feira)"""
        try:
            regra = self.restricoes_docentes[self.restricoes_docentes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                # Procura "Quarta-Feira" E "Noturno" na mesma string de restrição
                if dia in dias_indisp and turno in dias_indisp:
                    return True
        except:
            pass
        return False

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        conflitos = []
        semanas_solicitadas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave in self.ocupacao:
                if not semanas_solicitadas.isdisjoint(self.ocupacao[chave]):
                    conflitos.append(recurso)
        return conflitos

    def reservar(self, recursos, dia, turno, sem_ini, sem_fim):
        semanas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave not in self.ocupacao: self.ocupacao[chave] = set()
            self.ocupacao[chave].update(semanas)

    def buscar_sala_teorica(self, dia, turno, sem_ini, sem_fim):
        for sala in SALAS_TEORICAS_DISPONIVEIS:
            if not self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim):
                return sala
        return "Sala Teórica (A Definir)"

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        # Ordenação: Prioriza quem tem dia travado DE VERDADE (ex: professor externo)
        # Mas deixa UCs comuns para o fim para terem flexibilidade
        self.demandas['Prioridade'] = self.demandas.apply(lambda x: 1 if x['Dia_Travado'] else 2, axis=1)
        demandas_ordenadas = self.demandas.sort_values('Prioridade')
        
        total = len(demandas_ordenadas)
        bar = st.progress(0)

        for idx, row in demandas_ordenadas.iterrows():
            alocado = False
            
            # Parsing
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            id_turma = str(row['ID_Turma']).strip()
            ch_total = float(row['Carga_Horaria_Total'] or 0)
            duracao_ideal = int(np.ceil(ch_total / 4))
            
            # --- ESTRATÉGIA DE DIAS (NÍVEL 1) ---
            # Se tiver dia travado, começa por ele. Se falhar, tenta os outros.
            # Se NÃO tiver dia travado, tenta todos na ordem padrão.
            dias_preferencia = [row['Dia_Travado']] if row['Dia_Travado'] else dias_uteis
            if row['Dia_Travado']: # Adiciona os outros dias como backup
                dias_backup = [d for d in dias_uteis if d != row['Dia_Travado']]
                dias_tentativa = dias_preferencia + dias_backup
            else:
                dias_tentativa = dias_uteis

            motivo_falha = ""

            for dia in dias_tentativa:
                if alocado: break

                # Checa bloqueio fixo do docente (Hard Constraint)
                bloqueado = False
                for doc in docentes:
                    if self.verificar_bloqueio_docente(doc, dia, row['Turno']):
                        bloqueado = True
                        motivo_falha = f"Bloqueio Fixo de {doc} na {dia}"
                        break
                if bloqueado: continue

                # --- ESTRATÉGIA DE BLOCOS (NÍVEL 3) ---
                # Define pontos de partida: Início (Sem 1) e Meio (Sem 11)
                pontos_partida = [1]
                if duracao_ideal <= 11: # Se for curta, pode tentar começar no Bloco 2
                    pontos_partida.append(11)
                
                # Se o usuário forçou semana, respeita
                if row['Semana_Inicio']: pontos_partida = [int(row['Semana_Inicio'])]

                for inicio_bloco in pontos_partida:
                    if alocado: break
                    
                    # Ajuste Calendário (Semana 1 só Qui/Sex)
                    inicio_real = inicio_bloco
                    if inicio_real == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                        inicio_real = 2

                    # --- ESTRATÉGIA DE DESLOCAMENTO (NÍVEL 2) ---
                    # Tenta deslizar até 10 semanas para achar vaga
                    for shift in range(11):
                        sem_ini = inicio_real + shift
                        sem_fim = sem_ini + duracao_ideal - 1
                        
                        # Truncamento (Alocação Parcial)
                        if sem_fim > 22: sem_fim = 22
                        duracao_real = sem_fim - sem_ini + 1
                        if duracao_real <= 0: break

                        # Lógica de Recursos (Labs vs Teórica)
                        usa_lab = any(e in LABS_AB for e in espacos)
                        
                        rec_f1 = [] # Sem 1-3
                        rec_f2 = [] # Sem 4+
                        
                        if usa_lab and sem_ini < 4:
                            sala_t = self.buscar_sala_teorica(dia, row['Turno'], sem_ini, min(3, sem_fim))
                            rec_f1 = docentes + [sala_t, id_turma]
                            if sem_fim >= 4:
                                rec_f2 = docentes + espacos + [id_turma]
                        else:
                            rec_f2 = docentes + espacos + [id_turma]

                        # Checagem Final
                        conf_f1 = []
                        conf_f2 = []
                        if rec_f1: conf_f1 = self.verificar_disponibilidade(rec_f1, dia, row['Turno'], sem_ini, min(3, sem_fim))
                        sem_ini_f2 = max(4, sem_ini) if usa_lab else sem_ini
                        if rec_f2 and sem_fim >= sem_ini_f2:
                            conf_f2 = self.verificar_disponibilidade(rec_f2, dia, row['Turno'], sem_ini_f2, sem_fim)

                        if not conf_f1 and not conf_f2:
                            # SUCESSO!
                            if rec_f1: self.reservar(rec_f1, dia, row['Turno'], sem_ini, min(3, sem_fim))
                            if rec_f2 and sem_fim >= sem_ini_f2: self.reservar(rec_f2, dia, row['Turno'], sem_ini_f2, sem_fim)
                            
                            status = "✅ Alocado"
                            obs = ""
                            if (duracao_real * 4) < ch_total:
                                status = "⚠️ Parcial"
                                obs = f"Faltam {ch_total - (duracao_real*4)}h"

                            espaco_str = " + ".join(espacos)
                            if rec_f1:
                                sala_t = [r for r in rec_f1 if "Sala" in r][0]
                                espaco_str = f"{sala_t} (Sem {sem_ini}-3) -> {espaco_str}"

                            self.grade.append({
                                "ID_Turma": id_turma, "UC": row['Nome_UC'], "Dia": dia,
                                "Turno": row['Turno'], "Docentes": ", ".join(docentes),
                                "Espacos": espaco_str, "Semana_Inicio": sem_ini,
                                "Semana_Fim": sem_fim, "Status": status, "Obs": obs
                            })
                            alocado = True
                            break # Sai do Shift
                        else:
                            motivo_falha = f"Conflito: {list(set(conf_f1+conf_f2))}"
                    
                    if alocado: break # Sai do Bloco
                if alocado: break # Sai do Dia

            if not alocado:
                self.log_erros.append(f"❌ {id_turma} - {row['Nome_UC']}: {motivo_falha}")
                self.grade.append({"ID_Turma": id_turma, "UC": row['Nome_UC'], "Status": "❌ Erro", "Obs": motivo_falha})

            bar.progress((idx + 1) / total)

        return pd.DataFrame(self.grade), self.log_erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V9"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Otimização Concluída!")
        
        # ZIP
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade.csv", converter_df_para_csv(df_res))
            z.writestr("02_Erros.csv", converter_df_para_csv(pd.DataFrame(erros, columns=["Erro"])))
        
        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V9.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
