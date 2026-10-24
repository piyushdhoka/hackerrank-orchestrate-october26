"""Hand-written multilingual intent cases (participant-created; not from organizer data).

tier 'core': the deterministic first pass must already be right.
tier 'hard': indirect phrasing, uncovered languages or deletion words used for a non-deletion action;
             only the LLM gate is expected to get these right.
"""

CORPUS = [
    # ---- deletion requests -------------------------------------------------------------------
    ("Please wipe every trace of me from your shop, including old orders' contact details.", True, "en", "core"),
    ("Last month I asked you to erase my account. Can you confirm everything is actually gone?", True, "en", "core"),
    ("Under Article 17 GDPR I am exercising my right to erasure.", True, "en", "core"),
    ("Hola, please delete my account, gracias.", True, "en", "core"),
    ("Je souhaite que mon compte et toutes mes données personnelles soient supprimés.", True, "fr", "core"),
    ("Quiero darme de baja y que borren todos mis datos personales.", True, "es", "core"),
    ("Por favor, apaguem minha conta e todos os dados associados a ela.", True, "pt", "core"),
    ("Bitte löschen Sie mein Kundenkonto und alle gespeicherten Daten.", True, "de", "core"),
    ("Vi chiedo la cancellazione definitiva del mio account e dei miei dati personali.", True, "it", "core"),
    ("退会して、私の個人情報をすべて消去してください。", True, "ja", "core"),
    ("회원 탈퇴를 원합니다. 제 개인정보를 모두 삭제해 주세요.", True, "ko", "core"),
    ("请注销我的账户并删除所有个人信息。", True, "zh", "core"),
    ("أرجو حذف حسابي وجميع بياناتي الشخصية.", True, "ar", "core"),
    ("कृपया मेरा खाता और मेरी सारी निजी जानकारी हटा दें।", True, "hi", "core"),
    ("Verwijder alstublieft mijn account en al mijn persoonsgegevens.", True, "nl", "core"),
    ("Proszę o usunięcie mojego konta i wszystkich danych osobowych.", True, "pl", "core"),
    ("Lütfen hesabımı ve tüm kişisel verilerimi silin.", True, "tr", "core"),
    ("Прошу удалить мою учётную запись и все персональные данные.", True, "ru", "core"),
    ("Jag vill att ni raderar mitt konto och alla mina personuppgifter.", True, "sv", "core"),
    ("I'm done with this store. Get rid of my profile and anything you've kept about me.", True, "en", "hard"),
    ("Kontoyu kapatın, artık bu hizmeti kullanmayacağım ve bilgilerimin tutulmasını istemiyorum.", True, "tr", "hard"),
    ("Hãy xóa tài khoản và toàn bộ dữ liệu cá nhân của tôi.", True, "vi", "hard"),
    # ---- not deletion ------------------------------------------------------------------------
    ("Please don't delete anything — I just want to update my shipping address.", False, "en", "core"),
    ("Can I get a copy of all the data you hold about me? I'm not closing my account.", False, "en", "core"),
    ("Unsubscribe me from your newsletter, but keep my account open.", False, "en", "core"),
    ("Stop sending me promotional texts. That's all.", False, "en", "core"),
    ("Is it possible to download my order history as a CSV?", False, "en", "core"),
    ("Merci de ne pas supprimer mon compte ; je voudrais seulement une copie de mes données.", False, "fr", "core"),
    ("No quiero eliminar mi cuenta, solo cambiar el correo electrónico.", False, "es", "core"),
    ("Gostaria de receber uma cópia dos meus dados. Não excluam a conta, por favor.", False, "pt", "core"),
    ("Bitte korrigieren Sie meinen Nachnamen. Mein Konto bitte nicht löschen.", False, "de", "core"),
    ("Non cancellate il mio account: voglio solo smettere di ricevere email promozionali.", False, "it", "core"),
    ("アカウントは削除しないでください。登録メールアドレスを変更したいです。", False, "ja", "core"),
    ("계정은 삭제하지 말고 마케팅 이메일 수신만 중단해 주세요.", False, "ko", "core"),
    ("请不要删除我的账户，我只想更改收货地址。", False, "zh", "core"),
    ("لا تحذفوا حسابي، أريد فقط تحديث رقم هاتفي.", False, "ar", "core"),
    ("Remove my old phone number from the profile and add the new one.", False, "en", "hard"),
    ("I forgot my password and the reset link doesn't work.", False, "en", "hard"),
    ("Ik wil graag een kopie van mijn gegevens ontvangen.", False, "nl", "hard"),
    ("Пожалуйста, измените мой номер телефона в профиле.", False, "ru", "hard"),
    ("¿Pueden enviarme una copia de mis facturas del año pasado? Sigo usando la cuenta.", False, "es", "hard"),
]
