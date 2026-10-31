from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.sql import func
from datetime import datetime, timedelta, date
import csv
import io
from io import BytesIO
import requests 
import os
import smtplib
from email.mime.text import MIMEText
from pollinations import Pollinations
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

app = Flask(__name__)
app.config['SECRET_KEY'] = 'une_cle_secrete_tres_difficile_a_deviner'

# ==============================================================
# CONNEXION A LA BASE DE DONNÉES
# ==============================================================
db_url = os.environ.get('DATABASE_URL', 'sqlite:///avis.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 300}

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ==============================================================
# CONFIGURATION
EMAIL_EXPEDITEUR = "jsongtama@gmail.com" 
EMAIL_MOT_DE_PASSE = "nzyxcxmyliizdivv" # Ton code de 16 lettres
EMAIL_ADMIN = "jsongtama@gmail.com" 

CLE_API_POLLINATIONS = "sk_ZVxmfVqjO01Tm3Hxq67UcrXFZCQmasOm"
PRIX_BASE = 10000
TOKEN_APIFY = "apify_api_QaViakKtCu50tZaS70yNtZqhpRjT7CC2RUUpg"
# ==============================================================

# --- FONCTIONS D'ENVOI D'EMAIL ---
def envoyer_email_generique(destinataire, sujet, corps):
    if not destinataire: return
    try:
        msg = MIMEText(corps)
        msg['Subject'] = sujet
        msg['From'] = EMAIL_EXPEDITEUR
        msg['To'] = destinataire

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_EXPEDITEUR, EMAIL_MOT_DE_PASSE)
        server.send_message(msg)
        server.quit()
        print(f"✅ Email envoyé à {destinataire}")
    except Exception as e:
        print(f"❌ Erreur Envoi Email : {e}")

def envoyer_alerte_bad_buzz(email_destinataire, nom_entreprise, nom_client, note, texte_avis):
    message = f"🚨 ALERTE BAD BUZZ - {nom_entreprise}\n\n"
    message += f"Un client vient de laisser un avis négatif ({note}/5).\n\n"
    message += f"Client : {nom_client}\n"
    message += f"Avis : \"{texte_avis}\"\n\n"
    message += f"Connectez-vous vite sur Nzinah pour répondre."
    envoyer_email_generique(email_destinataire, f"🚨 Alerte : Nouvel avis négatif pour {nom_entreprise}", message)

# --- BASE DE DONNÉES ---
class CodePromo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    reduction = db.Column(db.Integer, nullable=False)
    nom_partenaire = db.Column(db.String(100), nullable=True)

class Utilisateur(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nom_entreprise = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    telephone = db.Column(db.String(20), nullable=True)
    mot_de_passe = db.Column(db.String(200), nullable=False)
    
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    plan = db.Column(db.String(20), default='Gratuit')
    date_expiration = db.Column(db.DateTime, nullable=True)
    
    code_promo = db.Column(db.String(50), nullable=True)
    prix_a_payer = db.Column(db.Integer, default=PRIX_BASE)
    
    sync_count = db.Column(db.Integer, default=0)
    sync_date = db.Column(db.Date, default=date.today)

    def jours_restants(self):
        if self.plan == 'Premium': return None
        fin_essai = self.date_inscription + timedelta(days=14)
        return max(0, (fin_essai - datetime.utcnow()).days)

    def est_actif(self):
        if self.plan == 'Premium':
            return self.date_expiration and self.date_expiration > datetime.utcnow()
        return self.jours_restants() > 0

class Avis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client = db.Column(db.String(100), nullable=False)
    plateforme = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Integer, nullable=False)
    texte = db.Column(db.Text, nullable=False)
    reponse = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('utilisateur.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return Utilisateur.query.get(int(user_id))

# --- ROUTES ---
@app.route('/')
def home():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    return render_template('home.html')

@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    if request.method == 'POST':
        email = request.form['email']
        nom = request.form['nom_entreprise']
        telephone = request.form['telephone']
        password = request.form['password']
        code_promo = request.form.get('code_promo', '').strip().upper()
        
        if Utilisateur.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé.", "error")
            return redirect(url_for('inscription'))
        
        prix_final = PRIX_BASE
        if code_promo:
            promo_en_bdd = CodePromo.query.filter_by(code=code_promo).first()
            if promo_en_bdd:
                prix_final = PRIX_BASE - promo_en_bdd.reduction
            
        nouvel_user = Utilisateur(
            nom_entreprise=nom, email=email, telephone=telephone, 
            mot_de_passe=generate_password_hash(password, method='pbkdf2:sha256'),
            code_promo=code_promo if code_promo else None, prix_a_payer=prix_final
        )
        db.session.add(nouvel_user)
        db.session.commit()
        login_user(nouvel_user)
        
        # 📧 EMAIL 1 : MESSAGE DE BIENVENUE POUR LE CLIENT
        msg_client = f"Bonjour {nom},\n\n"
        msg_client += f"Bienvenue sur Nzinah ! Votre compte a été créé avec succès.\n\n"
        msg_client += f"Vous bénéficiez de 14 jours d'essai gratuit pour découvrir toutes les fonctionnalités :\n"
        msg_client += f"- Importation de vos avis Google\n"
        msg_client += f"- Réponses par Intelligence Artificielle\n"
        msg_client += f"- Widget pour votre site web\n\n"
        if prix_final < PRIX_BASE:
            msg_client += f"Votre code promo '{code_promo}' a bien été appliqué. Votre premier mois vous coûtera {prix_final} FCFA.\n\n"
        
        msg_client += f"Pour commencer, connectez-vous ici : https://nzinah.onrender.com/login\n\n"
        msg_client += f"L'équipe Nzinah."
        envoyer_email_generique(email, "Bienvenue sur Nzinah - Votre essai a commencé !", msg_client)
        
        # 📧 EMAIL 2 : NOTIFICATION POUR TOI (L'ADMIN)
        msg_admin = f"🎉 Nouvelle inscription sur Nzinah !\n\n"
        msg_admin += f"Entreprise : {nom}\n"
        msg_admin += f"Email : {email}\n"
        msg_admin += f"Téléphone : {telephone}\n"
        msg_admin += f"Code promo utilisé : {code_promo if code_promo else 'Aucun'}\n"
        msg_admin += f"Montant à payer après essai : {prix_final} FCFA"
        envoyer_email_generique(EMAIL_ADMIN, f"🎉 Nouveau client : {nom}", msg_admin)
        
        if prix_final < PRIX_BASE:
            flash(f"Bienvenue ! Code promo appliqué. Vous paierez {prix_final} FCFA.", "success")
        else:
            flash("Bienvenue ! Votre essai de 14 jours a commencé.", "success")
            
        return redirect(url_for('accueil'))
    return render_template('inscription.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('accueil'))
    if request.method == 'POST':
        user = Utilisateur.query.filter_by(email=request.form['email']).first()
        se_souvenir = 'remember' in request.form
        if user and check_password_hash(user.mot_de_passe, request.form['password']):
            login_user(user, remember=se_souvenir)
            return redirect(url_for('accueil'))
        flash("Email ou mot de passe incorrect.", "error")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def accueil():
    if not current_user.est_actif():
        flash("Votre essai est terminé. Veuillez payer votre abonnement.", "error")
        return redirect(url_for('abonnement'))

    if request.method == 'POST':
        if 'repondre' in request.form:
            avis_id = request.form['avis_id']
            texte_reponse = request.form.get('texte_reponse', '').strip()
            if texte_reponse:
                avis_a_modifier = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
                if avis_a_modifier:
                    avis_a_modifier.reponse = texte_reponse
                    db.session.commit()
                    flash("Réponse publiée !", "success")
                    
        elif 'generer_ia' in request.form:
            avis_id = request.form['avis_id']
            avis_a_modifier = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
            if avis_a_modifier:
                try:
                    client_ia = Pollinations(api_key=CLE_API_POLLINATIONS)
                    prompt = f"Tu es le gérant d'une entreprise. Réponds poliment et professionnellement à cet avis client en français : '{avis_a_modifier.texte}'"
                    avis_a_modifier.reponse = client_ia.generate_text(prompt).strip()
                    db.session.commit()
                    flash("L'IA a généré une réponse !", "success")
                except Exception as e:
                    flash("L'IA a eu un problème. Réessayez.", "error")
                
        elif 'supprimer_avis' in request.form:
            avis_id = request.form['avis_id']
            avis_a_supprimer = Avis.query.filter_by(id=avis_id, user_id=current_user.id).first()
            if avis_a_supprimer:
                db.session.delete(avis_a_supprimer)
                db.session.commit()
                flash("Avis supprimé.", "success")
            
        return redirect(url_for('accueil'))

    tous_les_avis = Avis.query.filter_by(user_id=current_user.id).order_by(Avis.id.desc()).limit(5).all()
    total_avis = Avis.query.filter_by(user_id=current_user.id).count()
    note_moyenne = db.session.query(func.avg(Avis.note)).filter(Avis.user_id == current_user.id).scalar() or 0
    avis_negatifs = Avis.query.filter(Avis.user_id == current_user.id, Avis.note <= 2).count()

    return render_template('index.html', avis=tous_les_avis, total=total_avis, moyenne=round(note_moyenne, 1), negatifs=avis_negatifs)

@app.route('/tous-les-avis')
@login_required
def tous_les_avis():
    if not current_user.est_actif(): return redirect(url_for('abonnement'))
    avis = Avis.query.filter_by(user_id=current_user.id).order_by(Avis.id.desc()).all()
    return render_template('tous_les_avis.html', avis=avis)

@app.route('/importer', methods=['GET', 'POST'])
@login_required
def importer():
    if not current_user.est_actif(): return redirect(url_for('abonnement'))
    if request.method == 'POST':
        fichier = request.files.get('fichier_csv')
        if fichier and fichier.filename.endswith('.csv'):
            stream = io.StringIO(fichier.stream.read().decode("UTF8"), newline=None)
            csv_input = csv.reader(stream)
            next(csv_input, None)
            compteur = 0
            for row in csv_input:
                if len(row) >= 4:
                    try:
                        nouvel_avis = Avis(client=row[0], plateforme=row[1], note=int(row[2]), texte=row[3], user_id=current_user.id)
                        db.session.add(nouvel_avis)
                        compteur += 1
                        
                        if int(row[2]) <= 2:
                            envoyer_alerte_bad_buzz(current_user.email, current_user.nom_entreprise, row[0], int(row[2]), row[3])
                            
                    except: pass
            db.session.commit()
            flash(f'{compteur} avis importés !', "success")
            return redirect(url_for('tous_les_avis'))
        else:
            flash('Format invalide. Utilisez un fichier .csv', "error")
    return render_template('importer.html')

@app.route('/parametres', methods=['GET', 'POST'])
@login_required
def parametres():
    if request.method == 'POST':
        if 'modifier_nom' in request.form:
            nouveau_nom = request.form['nom_entreprise']
            if nouveau_nom.strip():
                current_user.nom_entreprise = nouveau_nom
                db.session.commit()
                flash("Nom mis à jour !", "success")
        elif 'modifier_mdp' in request.form:
            if check_password_hash(current_user.mot_de_passe, request.form['ancien_mdp']):
                current_user.mot_de_passe = generate_password_hash(request.form['nouveau_mdp'], method='pbkdf2:sha256')
                db.session.commit()
                flash("Mot de passe modifié !", "success")
            else:
                flash("Ancien mot de passe incorrect.", "error")
        return redirect(url_for('parametres'))
    return render_template('parametres.html')

@app.route('/abonnement')
@login_required
def abonnement():
    return render_template('abonnement.html')

@app.route('/widget')
@login_required
def widget():
    code_html = f'<script src="https://nzinah.onrender.com/widget-api/{current_user.id}"></script>'
    return render_template('widget.html', code_html=code_html)

@app.route('/widget-api/<int:user_id>')
def widget_api(user_id):
    user = Utilisateur.query.get(user_id)
    if not user: return "document.write('<p>Widget introuvable</p>');"
    avis = Avis.query.filter_by(user_id=user.id).all()
    if not avis: return "document.write('<p>Aucun avis</p>');"
    moyenne = sum([a.note for a in avis]) / len(avis)
    etoiles = '⭐' * round(moyenne)
    script_js = f"""
    document.write(`
        <div style="font-family: Arial, sans-serif; background: white; border: 1px solid #e5e7eb; padding: 15px; border-radius: 8px; display: inline-block; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <div style="font-size: 20px; margin-bottom: 5px;">{etoiles}</div>
            <div style="font-size: 14px; color: #6b7280;">
                <strong>{moyenne:.1f} / 5</strong> basé sur {len(avis)} avis clients
            </div>
        </div>
    `);
    """
    return script_js, 200, {'Content-Type': 'application/javascript'}

@app.route('/rapport-pdf')
@login_required
def rapport_pdf():
    avis = Avis.query.filter_by(user_id=current_user.id).all()
    total_avis = len(avis)
    moyenne = round(sum([a.note for a in avis]) / total_avis, 1) if total_avis > 0 else 0
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    p.setFont("Helvetica-Bold", 24)
    p.setFillColorRGB(0.06, 0.72, 0.51)
    p.drawString(2*cm, height - 3*cm, "Nzinah")
    p.setFont("Helvetica-Oblique", 14)
    p.setFillColorRGB(0.4, 0.4, 0.4)
    p.drawString(2*cm, height - 4*cm, f"Rapport - {current_user.nom_entreprise}")
    p.setFont("Helvetica", 12)
    p.setFillColorRGB(0, 0, 0)
    p.drawRightString(width - 2*cm, height - 3*cm, f"Date : {datetime.now().strftime('%d/%m/%Y')}")
    p.setFont("Helvetica-Bold", 16)
    p.drawString(2*cm, height - 6*cm, "Vos Statistiques Globales")
    p.setFont("Helvetica", 14)
    p.drawString(2*cm, height - 7*cm, f"Note moyenne : {moyenne} / 5")
    p.drawString(2*cm, height - 8*cm, f"Total des avis collectes : {total_avis}")
    p.showPage()
    p.save()
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=f'Rapport_{current_user.nom_entreprise}.pdf')

@app.route('/synchroniser-google')
@login_required
def synchroniser_google():
    aujourd_hui = date.today()
    if current_user.sync_date != aujourd_hui:
        current_user.sync_date = aujourd_hui
        current_user.sync_count = 0
        db.session.commit()

    limite = 10 if current_user.plan == 'Premium' else 3
    if current_user.sync_count >= limite:
        flash(f"Vous avez atteint votre limite de {limite} synchronisations par jour.", "error")
        return redirect(url_for('importer'))

    nom_entreprise = current_user.nom_entreprise
    url = "https://api.apify.com/v2/acts/compass~crawler-google-places/run-sync-get-dataset-items"
    headers = {"Authorization": f"Bearer {TOKEN_APIFY}"}
    payload = {
        "searchStringsArray": [nom_entreprise],
        "locationQuery": "Cameroun", 
        "maxReviews": 20,
        "language": "fr",
        "reviewsSort": "newest"
    }
    
    try:
        flash("Synchronisation avec Google en cours... Veuillez patienter.", "success")
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code in [200, 201]:
            data = response.json()
            if not data or (isinstance(data, list) and len(data) == 0):
                flash("❌ Restaurant introuvable. Vérifiez votre nom d'entreprise dans les Paramètres.", "error")
                return redirect(url_for('importer'))

            compteur = 0
            for lieu in data:
                if 'reviews' in lieu:
                    for avis in lieu['reviews']:
                        existe = Avis.query.filter_by(client=avis.get('name', 'Client Google'), texte=avis.get('text', ''), user_id=current_user.id).first()
                        if not existe:
                            nouvel_avis = Avis(client=avis.get('name', 'Client Google'), plateforme="Google", note=avis.get('stars', 5), texte=avis.get('text', ''), user_id=current_user.id)
                            db.session.add(nouvel_avis)
                            compteur += 1
                            
                            if avis.get('stars', 5) <= 2:
                                envoyer_alerte_bad_buzz(current_user.email, current_user.nom_entreprise, avis.get('name', 'Client'), avis.get('stars', 5), avis.get('text', ''))
            
            current_user.sync_count += 1
            db.session.commit()
            
            if compteur > 0: flash(f"✅ {compteur} nouveaux avis importés depuis Google !", "success")
            else: flash("Aucun nouvel avis à importer.", "success")
        else:
            flash(f"❌ Erreur Apify : {response.status_code}", "error")
    except Exception as e:
        flash(f"❌ Erreur de connexion : {e}", "error")
        
    return redirect(url_for('importer'))

@app.route('/admin-secret', methods=['GET', 'POST'])
@login_required
def admin_secret():
    if current_user.email != EMAIL_ADMIN:
        flash("Accès refusé.", "error")
        return redirect(url_for('accueil'))
        
    if request.method == 'POST':
        nouveau_code = request.form['code'].strip().upper()
        reduction = int(request.form['reduction'])
        partenaire = request.form['partenaire']
        if not CodePromo.query.filter_by(code=nouveau_code).first():
            promo = CodePromo(code=nouveau_code, reduction=reduction, nom_partenaire=partenaire)
            db.session.add(promo)
            db.session.commit()
            flash(f"Code promo '{nouveau_code}' créé !", "success")
        else:
            flash("Ce code promo existe déjà.", "error")
        return redirect(url_for('admin_secret'))

    utilisateurs = Utilisateur.query.all()
    codes_promo = CodePromo.query.all()
    return render_template('admin.html', utilisateurs=utilisateurs, codes_promo=codes_promo)

@app.route('/activer-client/<int:user_id>')
@login_required
def activer_client(user_id):
    if current_user.email != EMAIL_ADMIN: return redirect(url_for('accueil'))
    user = Utilisateur.query.get(user_id)
    if user:
        user.plan = 'Premium'
        user.date_expiration = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
        flash(f"Le compte de {user.nom_entreprise} a été activé !", "success")
    return redirect(url_for('admin_secret'))

# ==============================================================
# MISE A JOUR DE LA BASE DE DONNÉES (Colonnes Apify)
# ==============================================================
with app.app_context():
    db.create_all()
    try:
        db.session.execute(db.text('ALTER TABLE utilisateur ADD COLUMN sync_count INTEGER DEFAULT 0;'))
        db.session.execute(db.text('ALTER TABLE utilisateur ADD COLUMN sync_date DATE;'))
        db.session.commit()
    except Exception:
        db.session.rollback()

if __name__ == '__main__':
    app.run(debug=True)
